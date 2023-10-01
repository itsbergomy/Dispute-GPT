import pdfplumber
import pandas as pd
import re
import random
import openai
from dotenv import load_dotenv
import os


load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")

def extract_tables_from_pdf(file_path):
    with pdfplumber.open(file_path) as pdf:
        tables = []
        for page in pdf.pages:
            # Extract the tables from the page
            page_tables = page.extract_tables()
            for table in page_tables:
                tables.append(table)
        return tables

file_path = 'experian markus.pdf'
tables = extract_tables_from_pdf(file_path) 
# Convert the list of tables into a DataFrame
dfs = []
for table in tables:
    # The column names are in the first two rows of the table
    column_names = [item for sublist in table[:17] for item in sublist]

    df = pd.DataFrame(table[17:], columns=column_names)
    dfs.append(df)

# Concatenate all the DataFrames
df = pd.concat(dfs)



def extract_info_from_dataframe(df):

    # Define the patterns
    account_name_pattern = r"Account name (.+?) Balance"
    account_number_pattern = r"Account number (\S+) "
    status_pattern1 = r"Status ([A-Za-z\s]+)\. \$([\d,]+) written off\. \$([\d,]+) Terms -"
    status_pattern2 = r"Status ([A-Za-z\s]+)\. \$([\d,]+) past due"

  
    # Store the results
    results = []

    # Iterate over the rows of the DataFrame
    for column_name in df.columns:
        # Convert the row to text
        text = str(column_name)

        # Search for the patterns in the text
        account_name_match = re.search(account_name_pattern, text)
        account_number_match = re.search(account_number_pattern, text)
        status_match1 = re.search(status_pattern1, text)
        status_match2 = re.search(status_pattern2, text)

        # If a match is found, append it to the results
        if account_name_match:
            results.append(("Account name", account_name_match.group(1)))
        if account_number_match:
            results.append(("Account number", account_number_match.group(1)))
        if status_match1:
            results.append(("Status", status_match1.group(1)))
        if status_match2:
            results.append(("Status", status_match2.group(1)))

    return results

# Define the entities
entities = ["Experian", "TransUnion", "Equifax"]

# Defining actions and issues
actions = {
    "handling credit report mix-ups": ["correcting credit report inaccuracies", "identitfy confusion in credit reports", "inconsistent billing/payment history across all three credit bureaus"],
    "an alleged violation of 15 U.S. Code 1692g (Validation of Debts)": ["disputing unverified debts", "requesting debt validation", "challenging debt collection practices"],
    "challenging unverified collection accounts": ["collections without proper validation", "disputing unverified debts", "unrecognized collection accounts", "discrepancies in collection amounts"],
    "requesting an investigation into Uniform Commercial Code (UCC) violations related to my credit report": ["incorrect lien reporting", "misrepresentation of credit terms", "unauthorized sales of credit data"],
    "disputing late payments due to billing errors": ["challenging late payments resulting from incorrect billing statements", "rectifying late payments caused by discrepancies in account balances", "resolving late payments due to misapplied payments"],
    "handling charge-offs on credit card accounts": ["managing charge-offs on credit card accounts with multiple missed payments", "negotiating settlements with credit card issuers to clear charge-offs", "requesting a pay-for-delete agreement for credit card charge-offs"],
    "late payments on student loans": ["dealing with late payments on federal or private student loans", "seeking loan rehabilitation or consolidation after late payments", "understanding the impact of student loan late payments on credit"],
    "disputing medical bill charge-offs": ["challenging charge-offs resulting from medical billing disputes", "requesting validation of medical debts before addressing charge-offs", "understanding the impact of student loan late payments on credit"],
    "handling auto loan charge-offs": ["addressing charge-offs on auto loans and their impact on vehicle ownership", "negotiating with auto lenders for loan settlements or payment plans", "seeking to re-finance an auto loan after a charge-off"],
    "late payments on personal loans": ["managing late payments on personal loans from banks or online lenders", "exploring options to bring personal loan accounts current", "understanding the terms and conditions of personal loan late fees"],
    "disputing charge-offs due to economic hardship": ["requesting consideration for charge-off removal due to financial hardship", "documenting and explaining the circumstances that led to charge-offs", "exploring legal protections for consumers facing financial difficulties"],
    "late payments on utility bills": ["addressing late payments on utility bills", "negotiating payment plans with utility providers to avoid further issues", "understanding the impact of utility late payments on credit reports"],
    "handling charge-offs on retail store credit cards": ["managing charge-offs on retail store credit cards with high-interest rates", "negotiating settlements or payment arrangements with retail card issuers", "requesting removal of charge-offs after resolving retail card debts"],
    "disputing late payments on rent": ["addressing late rent payments and potential eviction consequences", "negotiating with landlords to remove late payment entries from rental history", "understanding the importance of timely rent payments for future rentals"]


}

 # Define the prompt templates
prompt_templates = [
        "Write a letter {action} for {entity} regarding {issue}. The account is {account_name} with account number {account_number} and has the following account status: {marks}",
        "I need a letter {action} for {entity} about an issue regarding {issue}. The account is {account_name} with account number {account_number} and has the following account status: {marks}",
        "Compse a letter {action} for {entity} about {issue}. The account is {account_name} with {account_number} and has the following account status: {marks}",
        "Draft a letter addressing a potential violation of {issue} pertaining to {action}, directed towards {entity}. The account is {account_name} with {account_number} and has the following account status: {marks}",
        "Compose a follow-up letter to {entity} addressing {issue} when the credit bureaus fail to respond to my initial dispute letter. The account is {account_name} with {account_number} and has the following account status: {marks}"
    ]

# Function to allow the user to choose an account
def choose_account(extracted_info_list):
    while True:  # Keep asking until a valid account is chosen
        print("Choose an account:")
        for i, account in enumerate(extracted_info_list, 1):
            account_name = account[0][1] if len(account) > 0 and account[0][0] == 'Account name' else 'Unknown'
            account_number = account[1][1] if len(account) > 1 and account[1][0] == 'Account number' else 'Unknown'
            print(f"{i}. {account_name} ({account_number})")
        
        selected_account_index = int(input("Enter the number of the account you want to choose: ")) - 1

        # Check if the selected index is valid
        if 0 <= selected_account_index < len(extracted_info_list):
            # Retrieve the selected account using the index
            selected_account = extracted_info_list[selected_account_index]
            return selected_account
        else:
            print("Invalid account choice. Please try again.")



def generate_dispute_letters(chosen_account, actions):
    # Initialize account_name, account_number, and marks
    account_name = account_number = marks = 'Unknown'

    if chosen_account is not None:
        print(chosen_account)
        # Extract account name and number from the original extracted_info
        account_name = next((info[1] for info in chosen_account if info[0] == 'Account name'), None)
        account_number = next((info[1] for info in chosen_account if info[0] == 'Account number'), None)

        # Filter the extracted info to only include items with "charge off" or "collection" in the status
        filtered_info = [info for info in chosen_account if 'Status' in info[0] and ('Account charged off' in info[1] or 'Collection account' in info[1])]
        # Convert the filtered info into a string of marks
        marks = ', '.join([f"{info[1]}" for info in filtered_info])
    else:
        print("Invalid account choice. Please try again.")
    
    class ContinueWithNextEntity(Exception):
        pass
    try:

        # Iterate over the entities
        for entity in entities:
                
                    # Ask the user to confirm the entity
                    confirm_entity = input(f"Is the entity {entity} correct? (yes/no): ")
                    if confirm_entity.lower() != "yes":
                        continue
                    # Generate a letter for the confirmed entity and account
                    action = input(f"Enter the action for the {entity} letter (e.g., Dispute, Request Information): ")
                    issue = input(f"Enter the issue for the {entity} letter (e.g., Incorrect balance, Wrong account status): ")
                    # Select a random prompt template
                    prompt_template = random.choice(prompt_templates)

                    # Fill in the randomly selected prompt template with the action, entity, issue, account name, account_number, and marks
                    prompt = prompt_template.format(action=action, entity=entity, issue=issue, account_name=account_name, account_number=account_number, marks=marks)

                    # Generate the dispute letter using the OpenAI API
                    response = openai.ChatCompletion.create(
                        model="gpt-3.5-turbo",
                        messages=[
                            {
                                "role": "system",
                                "content": "You are DisputeGPT, a bot that creates credit dispute letters. Use your knowledge of UCC, CFPB regulations, and USC to write compelling letters that address inaccuracies and potential infringements by creditors."
                            },
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],
                        max_tokens=2500
                    )
                    dispute_letter = response['choices'][0]['message']['content']

                    print(dispute_letter)

                    print("\n--- Next Letter ---\n")  # Print a separator between account letters

    except ContinueWithNextEntity:
        pass
                    
                    
    
'''
# Print each DataFrame with a divider
for i, df in enumerate(dfs):
    print(f"---------- DataFrame {i+1} ----------")
    print(df)
    print(f"---------- End of DataFrame {i+1} ----------\n")
'''
# Use the function to extract info and generate dispute letters
extracted_info_list = []
filtered_info_list = []

for df in dfs:
    extracted_info = extract_info_from_dataframe(df)
    # Check if the extracted info contains a 'Status' that is either 'Account charged off' or 'Collection account'
    if any('Status' in info[0] and ('Account charged off' in info[1] or 'Collection account' in info[1]) for info in extracted_info):
        extracted_info_list.append(extracted_info)

   

# Generate and print the dispute letters after all the extracted info has been printed
for extracted_info in extracted_info_list:
    print("---------- Extracted Info ----------")
    print(extracted_info)
    print("---------- End of Extracted Info ----------\n") 


# Let the user choose an account
chosen_account = choose_account(extracted_info_list)

# Generate and print the dispute letters
generate_dispute_letters(chosen_account, actions)
