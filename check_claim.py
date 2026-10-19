import csv, os
from dotenv import load_dotenv
from langsmith import traceable
from langsmith.wrappers import wrap_openai
from openai import OpenAI

load_dotenv()
CSV = "data/aws_reinvent_candidate_items.csv"
client = wrap_openai(OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"]))


@traceable(name="check_claim")
def check_claim(candidate_id: str) -> str:
    row = next(r for r in csv.DictReader(open(CSV, encoding="utf-8")) if r["candidate_id"] == candidate_id)
    resp = client.chat.completions.create(
        model=os.environ["OPENROUTER_MODEL"],
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": f"Is this a real AWS launch? Answer REAL, NOT REAL, or UNVERIFIABLE, then name the page that proves it. Give a URL only if you are confident it exists; otherwise say so.\n\n"
                       f"Claim: {row['claim_text']}\nSource: {row['origin_source_name']} ({row['origin_source_type']}, {row['origin_published_date']})",
        }],
    )
    return resp.choices[0].message.content


if __name__ == "__main__":
    print(check_claim("C-02"))
