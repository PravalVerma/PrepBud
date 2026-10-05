<!-- version: 1.1 -->
## Task: summarise the session

Write a short, warm summary (3–4 sentences, plain text, no headings) of a study session for the
student, in the second person.

Session facts:
- Duration: {{ duration_minutes }} minutes
- Questions answered: {{ questions_answered }} (accuracy {{ accuracy_percent }}%)
- End reason: {{ end_reason }}
{% for c in concepts %}
- {{ c.name }}: mastery {{ c.from_percent }}% → {{ c.to_percent }}%
{% endfor %}

Mention what improved (or what was tried, if scores were low), one thing to focus on next, and
encourage them to come back. Frame low scores as a starting point; never use discouraging words
such as "unfortunately" or "failed".
