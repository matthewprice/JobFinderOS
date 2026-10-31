You are labeling job postings to build a ground-truth set for evaluating a job-discovery system. For each posting, decide whether it is a role this specific candidate should see.

You will be given the candidate's own description of what they are looking for and a batch of postings (title, company, location, and the first part of the description). You are NOT given the system's search terms or score thresholds, and you should not guess at them. Judge each posting on its own merits against the candidate's stated goals.

Labels:
- "fit": the role family, seniority, and scope match what the candidate wants. Location and pay do not need to be perfect; a strong role in a city the candidate did not name is still "fit" if the candidate's rules allow it (for example, remote-eligible).
- "not_fit": wrong function, wrong seniority band (for example an individual-contributor posting when the candidate wants to lead a team, or an entry-level posting when they want senior scope), or an explicitly excluded company or location rule.
- "borderline": the title is adjacent or the scope is ambiguous from the text; a reasonable recruiter could argue either way.

Be strict about seniority when the candidate states a seniority rule, and read the description, not just the title: a "Manager" who builds a team from zero counts as leadership; a "Lead" with no reports does not.

For each posting return its req_id unchanged, the label, a confidence from 0 to 1, and a rationale of at most 25 words.

## Candidate

{candidate_brief}

## Postings

{postings}
