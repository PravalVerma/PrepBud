"""SQLAlchemy ORM models for all 23 tables in DATA_MODEL.md.

Importing this package registers every table on `Base.metadata` (used by Alembic).
"""

from app.db.models.ai import AIInteraction, AITrace
from app.db.models.assessment import (
    Misconception,
    Question,
    QuestionAttempt,
    StudentMisconception,
)
from app.db.models.concept import Concept, ConceptRelationship
from app.db.models.content import Document, DocumentSection, DocumentSectionConcept
from app.db.models.curriculum import Chapter, Course, Section, Subject
from app.db.models.mastery import StudentConceptMastery
from app.db.models.session import LearningGoal, LearningSession, SessionEvent
from app.db.models.study_plan import ReviewItem, StudyPlan
from app.db.models.user import StudentProfile, User

__all__ = [
    "AIInteraction",
    "AITrace",
    "Chapter",
    "Concept",
    "ConceptRelationship",
    "Course",
    "Document",
    "DocumentSection",
    "DocumentSectionConcept",
    "LearningGoal",
    "LearningSession",
    "Misconception",
    "Question",
    "QuestionAttempt",
    "ReviewItem",
    "Section",
    "SessionEvent",
    "StudentConceptMastery",
    "StudentMisconception",
    "StudentProfile",
    "StudyPlan",
    "Subject",
    "User",
]
