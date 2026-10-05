<!-- version: 1.0 -->
## Task: generate questions

Concept: {{ concept_name }}
Description: {{ concept_description or "(no description)" }}
Student level: {{ student_level }}
Target difficulty: {{ target_difficulty }} (0.0 = trivial, 1.0 = very hard)
Question type: {{ question_type }}

{% if content_snippets %}
Learning material (reference only):
---MATERIAL---
{{ content_snippets }}
---END MATERIAL---
{% else %}
No learning material is available; use standard, well-established knowledge of the concept.
{% endif %}

{% if misconceptions %}
Misconceptions this student has shown (good targets for distractors):
{% for m in misconceptions %}
- {{ m.name }}: {{ m.description }}
{% endfor %}
{% endif %}

{% if avoid %}
Do not repeat these recent questions:
{% for q in avoid %}
- {{ q }}
{% endfor %}
{% endif %}

Write {{ count }} different question(s) that test exactly this concept at about the target
difficulty. Types:
- "mcq": 4 options, exactly one correct; tag each wrong option with the misconception it
  catches (a short snake_case name) or null.
- "true_false": a statement; correct_answer is true or false.
- "short_answer" / "worked_problem" / "open_ended": correct_answer is the expected answer
  (for worked problems, the final result; put the steps in "explanation").
Give up to 2 hints that guide without revealing the answer.

Respond with JSON of this exact shape:
{"questions": [{"content": "...", "type": "{{ question_type }}", "difficulty": 0.5,
  "correct_answer": "...", "explanation": "...",
  "options": [{"label": "A", "text": "...", "is_correct": false, "misconception": "name_or_null"}],
  "hints": ["..."], "misconceptions_tested": ["..."]}]}
("options" only for mcq.)
