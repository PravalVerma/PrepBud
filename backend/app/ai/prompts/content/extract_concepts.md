<!-- version: 1.0 -->
Subject context: {{ subject_name or "unspecified" }}
Course context: {{ course_name or "unspecified" }}

Below are {{ chunks | length }} numbered chunk(s) of a learning material, delimited by
---CHUNK n--- markers.

{% for chunk in chunks %}
---CHUNK {{ chunk.number }}---{% if chunk.heading %} (section: {{ chunk.heading }}){% endif %}{% if chunk.pages %} (pages: {{ chunk.pages | join(", ") }}){% endif %}

{{ chunk.text }}

{% endfor %}
---END OF MATERIAL---

Extract the key learning concepts taught in these chunks. Each concept must be:
- Assessable: you can write a question that tests exactly this concept.
- Teachable: it can be explained in 2–5 minutes.
- Distinguishable: it is meaningfully different from the other concepts.
Avoid concepts that are too broad (e.g. "Algebra") or too trivial (e.g. "2 + 2 = 4").
Return at most {{ max_concepts }} concepts, most important first.

For each concept provide:
- "name": a concise name (3–8 words)
- "description": one paragraph describing the concept as taught in the material
- "difficulty_estimate": "low" | "medium" | "high"
- "prerequisites": names of concepts a student must understand first (may name concepts
  outside this list)
- "relationships": other concepts it relates to, each {"concept": name, "type": one of
  "related" | "generalisation" | "specialisation"} where the type describes the OTHER concept
  relative to this one
- "chunks": the chunk numbers where the concept is taught or used

Respond with JSON of this exact shape:
{"concepts": [{"name": "...", "description": "...", "difficulty_estimate": "medium",
  "prerequisites": ["..."], "relationships": [{"concept": "...", "type": "related"}],
  "chunks": [1]}]}
