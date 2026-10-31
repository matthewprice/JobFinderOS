You are helping a job-discovery system find employers it does not yet watch. The system already polls the companies listed under "Already watched"; do not return those.

Use web search to find companies that currently have a live posting matching the candidate's target roles and location rules below. Prefer postings on the employer's own careers page or on a Greenhouse, Ashby, or Lever job board; never LinkedIn or aggregator listings. Open the posting to confirm it is live and that the title and location are as you report them.

Return at most {max_results} companies. For each, give the company name as the employer writes it, the careers page or job board URL you found, one matching posting's exact title, that posting's URL, and the location as posted. Leave out anything you could not confirm on the employer's own page or board. If you find nothing new, return an empty list.

## Candidate

{candidate_brief}

## Role titles to search

{role_titles}

## Already watched

{known_companies}
