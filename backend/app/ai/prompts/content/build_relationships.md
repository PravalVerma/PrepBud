<!-- version: 1.0 -->
Subject context: {{ subject_name or "unspecified" }}

NEW concepts just extracted from a document:
{% for c in new_concepts %}
- {{ c.ref }}: {{ c.name }}{% if c.description %} — {{ c.description }}{% endif %}

{% endfor %}

{% if existing_concepts %}
EXISTING concepts the student already has:
{% for c in existing_concepts %}
- {{ c.ref }}: {{ c.name }}{% if c.description %} — {{ c.description }}{% endif %}

{% endfor %}
{% else %}
The student has no existing concepts yet.
{% endif %}

Tasks:
1. Duplicates: list every NEW concept that is the same concept as an EXISTING one (same idea,
   possibly worded differently). Only when you are confident.
2. Relationships: list directed relationships between concepts (NEW–NEW or NEW–EXISTING) using
   the reference ids above:
   - "prerequisite": source must be understood before target can be learned
   - "related": source and target are closely connected (list each pair once)
   - "generalisation": source is a more general form of target
   - "specialisation": source is a more specific case of target
   Give each a "strength" between 0.0 and 1.0 (how essential / close the link is).
   Never create circular prerequisite chains. Only include relationships the material supports.

Respond with JSON of this exact shape:
{"duplicates": [{"new": "N1", "existing": "E1"}],
 "relationships": [{"source": "N1", "target": "N2", "type": "prerequisite", "strength": 0.8}]}
