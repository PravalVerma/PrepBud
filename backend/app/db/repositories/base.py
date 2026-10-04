"""Repository base classes enforcing user-scoped data access (SECURITY_MODEL §3.1).

Every query a repository issues is filtered by the current user. A record that
exists but belongs to someone else is indistinguishable from a missing record:
both come back as ``None`` and surface to the API as 404.

Two flavours:

* `BaseRepository` — models with their own ``user_id`` column.
* `OwnedViaParentRepository` — models whose ownership is inherited through a
  join chain (e.g. ``sections → chapters → courses → subjects.user_id``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base


@dataclass(frozen=True, slots=True)
class PageRequest:
    page: int = 1
    per_page: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: Sequence[T]
    total: int
    page: int
    per_page: int


class ScopedRepository[ModelT: Base]:
    model: type[ModelT]
    default_order_by: ClassVar[tuple[Any, ...]] = ()

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _owned(self, user_id: uuid.UUID) -> Select[ModelT]:
        raise NotImplementedError

    async def get_by_id(self, id_: uuid.UUID, user_id: uuid.UUID) -> ModelT | None:
        id_col: Any = getattr(self.model, "id")  # noqa: B009 - generic over models
        stmt = self._owned(user_id).where(id_col == id_)
        return (await self.session.scalars(stmt)).one_or_none()

    async def paginate(
        self,
        user_id: uuid.UUID,
        page: PageRequest,
        *filters: ColumnElement[bool],
    ) -> Page[ModelT]:
        base = self._owned(user_id).where(*filters)
        total = await self.session.scalar(
            select(func.count()).select_from(base.order_by(None).subquery())
        )
        stmt = base.order_by(*self.default_order_by).offset(page.offset).limit(page.per_page)
        items = (await self.session.scalars(stmt)).all()
        return Page(items=items, total=total or 0, page=page.page, per_page=page.per_page)

    async def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def update(self, obj: ModelT, values: dict[str, Any]) -> ModelT:
        for key, value in values.items():
            setattr(obj, key, value)
        await self.session.flush()
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.flush()


class BaseRepository[ModelT: Base](ScopedRepository[ModelT]):
    """Repository for models carrying a ``user_id`` column."""

    def _owned(self, user_id: uuid.UUID) -> Select[ModelT]:
        user_col: Any = getattr(self.model, "user_id")  # noqa: B009 - generic over models
        return select(self.model).where(user_col == user_id)

    async def create(self, user_id: uuid.UUID, **values: Any) -> ModelT:
        return await self.add(self.model(user_id=user_id, **values))


class OwnedViaParentRepository[ModelT: Base](ScopedRepository[ModelT]):
    """Repository for models whose owner is found by joining up to a parent.

    Subclasses declare ``ownership_joins`` (``(target, onclause)`` pairs walked in
    order from the model) and ``owner_column`` (the ``user_id`` column reached at
    the end of the chain).
    """

    ownership_joins: ClassVar[tuple[tuple[Any, Any], ...]]
    owner_column: ClassVar[Any]

    def _owned(self, user_id: uuid.UUID) -> Select[ModelT]:
        stmt = select(self.model)
        for target, onclause in self.ownership_joins:
            stmt = stmt.join(target, onclause)
        # Read via the class: on an instance, the mapped attribute's descriptor would
        # try to bind to the repository object.
        owner_column = type(self).owner_column
        return stmt.where(owner_column == user_id)
