import openai
import json
import random
from faker import Faker
from dotenv import load_dotenv
import os

fake = Faker()

load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")

def generate_content(prompt, max_tokens=2500):
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "system",
                "content": "You are uDispute, a bot that creates credit dispute letters. Use your knowledge of UCC, CFPB regulations, and USC to write compelling letters that address inaccuracies and potential infringements by creditors."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        max_tokens=max_tokens
    )
    return response['choices'][0]['message']['content']

def generate_assistant_content(user_prompt):
    example_prompt = f"""
    Example:
    User: Create a dispute letter that highlights potential violations made by the credit bureaus, and highlight any inaccuracies mentioned for a closed installment loan, include the following: \n\n[Current Date]\n\n[Your First Name] [Your Last Name]\n[Your Address]\n\n[Credit Bureau]\n[Credit Bureau Address]\n\n[Account Name]: {fake.company()}\n[Account Number]: {fake.random_number(digits=10, fix_len=True)}\n\n[Dispute Description]: {fake.text(max_nb_chars=200)}
    
    Assistant: "{fake.date()}\n{fake.name()}\n{fake.address()}\n{fake.company()}\nP.O. Box {fake.random_number(digits=4, fix_len=True)}\n{fake.city()}, {fake.state_abbr()} {fake.zipcode()}\n\nTo Whom It May Concern at {fake.company()},\n\nI am writing to dispute inaccuracies on my credit report associated with an account from {fake.company()} (Account Number: {fake.random_number(digits=10, fix_len=True)}). I request an investigation into these inaccuracies as per the FCRA.\n\nThe discrepancies across all three bureaus—TransUnion, Equifax, and Experian—indicate a potential failure to deliver 'maximum possible accuracy.'\n\nBased on my examination of the reports, the inconsistencies are as follows:\n\n- TransUnion reported no payment data for the account from January to June 2021.\n- Equifax and Experian only presented payment data starting in June 2021.\n- For the year 2022, all three bureaus reported a 60-day late payment in January. However, TransUnion failed to provide any payment data for the remainder of the year, and Experian wrongly reported that the account was closed in February 2022.\n\nI urge your bureau to investigate these inaccuracies and to correct them promptly. If these issues cannot be substantiated, please remove these inaccuracies from my report as the FCRA guidelines dictate.\n\nI expect 'maximum possible accuracy,' as dictated by 15 U.S. Code § 1681e, for my future credit reports furnished by your agency.\n\nPlease provide an updated and corrected copy of my credit report by mail once these violations are rectified.\n\nSincerely,\n\n{fake.name()}"
    """
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {
                "role": "system",
                "content": "You are uDispute, a bot that creates credit dispute letters. Use your knowledge of UCC, CFPB regulations, and USC to write compelling letters that address inaccuracies and potential infringements by creditors."
            },
            {
                "role": "user",
                "content": example_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ],
        max_tokens=2500
    )
    return response['choices'][0]['message']['content']

def generate_user_prompt(template, **kwargs):
    return template.format(**kwargs)

def generate_dataset_entry(action, entity, issue):
    system_prompt = "You are uDispute, a bot that creates credit dispute letters. Use your knowledge of UCC, CFPB regulations, and USC to write compelling letters that address inaccuracies and potential infringements by creditors."
    
    user_prompt_templates = [
        "Write a letter {action} for {entity} regarding {issue}",
        "I need a letter {action} to {entity} about an issue regarding {issue}",
        "Compse a letter {action} for {entity} about {issue}?",
        "I'm looking to write a letter {action} to {entity} about {issue}.",
        "Draft a letter addressing a potential violation of {issue} pertaining to {action}, directed towards {entity}.",
        "Craft a letter addressing {action} and a potential violation of {issue} directed towards {entity}.",
        "Compose a follow-up letter to {entity} addressing {issue} when the credit bureaus fail to respond to my initial dispute letter."
        # Add more templates as needed
    ]
    
    user_prompt_template = random.choice(user_prompt_templates)
    user_prompt = generate_user_prompt(user_prompt_template, action=action, entity=entity, issue=issue, account_name=fake.company(), account_number=fake.random_number(digits=10, fix_len=True), date=fake.date(), name=fake.name(), address=fake.address(), city=fake.city(), state=fake.state_abbr(), zipcode=fake.zipcode())
    assistant_prompt = generate_assistant_content(user_prompt)


    entry = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": assistant_prompt}
        ]
    }
    return entry

def main():
    print("Program started...")

    actions = {
        "to place a freeze on my credit profile": ["identity theft", "fraudulent accounts", "billing error", "suspicious credit inquiries"],
        "when the credit bureaus do not respond to my initial dispute letter": ["late response (More than 30 days)", "failure to correct inaccuracies", "non-compliance with dispute resolution"],
        "challenging unverified collection accounts": ["collections without proper validation", "disputing unverified debts", "unrecognized collection accounts", "discrepancies in collection amounts"],
        "negotiating settlements with creditors": ["debt settlement negotiations", "reducing outstanding debt", "managing creditor agreements"],
        "disputing public records errors": ["incorrect bankruptcy information", "inaccurate tax liens", "clearing public records from credit report", "fraudulent person information"],
        "handling credit report mix-ups": ["correcting credit report inaccuracies", "identitfy confusion in credit reports", "inconsistent billing/payment history across all three credit bureaus"],
        "handling multiple inquiries from the same lender": ["redundant credit inquiries", "impact on credit score"],
        "an alleged violation of 15 U.S. Code 1692g (Validation of Debts)": ["disputing unverified debts", "requesting debt validation", "challenging debt collection practices"],
        "violation of 15 U.S Code 1681b (Permissible Purposes of Consumer Reports)": ["unauthorized credit inquiries", "disputing credit report access", "challenging credit report disclosures"],
        "requesting an investigation into Uniform Commercial Code (UCC) violations related to my credit report": ["incorrect lien reporting", "misrepresentation of credit terms", "unauthorized sales of credit data"],
        "disputing late payments due to billing errors": ["cahllenging late payments resulting from incorrect billing statements", "rectifying late payments caused by discrepancies in account balances", "resolving late payments due to misapplied payments"],
        
        # Add more actions and corresponding issues as needed
    }
    entities = ["Experian", "TransUnion", "Equifax"]

    with open("Finetune GPT3.txt", "a") as f:
        for _ in range(100):  # Generate 100 entries
            action = random.choice(list(actions.keys()))
            issue = random.choice(actions[action])
            entity = random.choice(entities)
            dataset_entry = generate_dataset_entry(action, entity, issue)
            print(json.dumps(dataset_entry, indent=4), file=f)

    print("Program finished.")

if __name__ == "__main__":
    main()