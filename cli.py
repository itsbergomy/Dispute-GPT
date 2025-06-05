import argparse
import os
import openai
import pandas as pd
from dotenv import load_dotenv
from colorama import Fore, Style



def main():
    load_dotenv()
    openai.api_key = os.getenv("OPENAI_API_KEY")


    # Create a top-level parser
    parser = argparse.ArgumentParser(description="Dispute GPT")
    parser.add_argument('--pdf_path', type=str, help='The path to the PDF file (required in dispute mode)', required=False)
    parser.add_argument('--mode', '-m', choices=['dispute', 'chat', 'manual'], help='The mode to enter (dispute, chat or manual)')

    
    # Parse the command-line arguments
    args, _ = parser.parse_known_args()

    print(f"{Fore.CYAN} Selected mode: {args.mode}")
    divider = "---------------------------------------------------------------"
    print(divider)
    print(f"{Fore.LIGHTWHITE_EX} Welcome to the Dispute GPT!")
    divider = "---------------------------------------------------------------"
    print(divider)
    print(f"{Fore.LIGHTWHITE_EX}This tool is designed to help users generate dispute letters by extracting derogatory accounts from credit profile PDF (Experian, mainly) allowing the user to pick which account they want to dispute, create a custom prompt with a custom action & issue or dispute reason then having GPT 3.5 take care of the letter creation.")  # Replace with your actual instructions
    divider = "---------------------------------------------------------------"
    print(divider)
    print(f"{Fore.LIGHTYELLOW_EX}Usage:")
    print(f"{Fore.LIGHTYELLOW_EX}1. To generate dispute letters, run {Fore.LIGHTRED_EX}'dispute' mode with 'python cli.py --mode dispute --pdf_path /path/to/your/file.pdf")
    print(f"{Fore.LIGHTYELLOW_EX}2. To enter chat mode for interactive conversation, {Fore.LIGHTRED_EX} use the -c or --chat option. {Fore.LIGHTYELLOW_EX}In chat mode, you can ask questions and get assistance from the AI.")
    print(f"{Fore.LIGHTYELLOW_EX}3. Type 'quit' to exit chat mode at any time.")
    divider = f"{Fore.LIGHTWHITE_EX}---------------------------------------------------------------"
    print(divider)
    mode = args.mode

    
    if mode == 'chat':
        def chat_with_openai(message):
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": message},
                ]
            )
            return response['choices'][0]['message']['content']

        print(f"{Fore.BLUE}Entering chat mode. Type 'quit' to exit.")
        while True:
            user_message = input(f"{Fore.CYAN}User: ")
            if user_message.lower() == "quit":
                break
            response = chat_with_openai(user_message)
            print(f"{Fore.LIGHTMAGENTA_EX}AI: ", response)
            print(divider)
            
        return
    
    
    elif mode == 'dispute':
        pdf_path = args.pdf_path
        if pdf_path is None:
            print(f"{Fore.LIGHTMAGENTA_EX}Error: No PDF file specified.")
            return
        if not os.path.isfile(pdf_path):
             print(f"{Fore.LIGHTRED_EX}Error: The specified file '{pdf_path}' does not exist.")
             return
        
        from test import extract_tables_from_pdf, extract_info_from_dataframe

        def process_tables(tables):
            dfs = []
            for table in tables:
                column_names = [item for sublist in table[:17] for item in sublist]
                df = pd.DataFrame(table[17:], columns=column_names)
                dfs.append(df)
            return dfs

        tables = extract_tables_from_pdf(args.pdf_path)
        dfs = process_tables(tables)

        extracted_info_list = []
        for df in dfs:
            extracted_info = extract_info_from_dataframe(df)
            if any('Status' in info[0] and ('Account charged off' in info[1] or 'Collection account' in info[1]) for info in extracted_info):
                extracted_info_list.append(extracted_info)
       

    elif mode == 'manual':
        # Prompt the user to manually enter the account details
        account_name = input("Enter the account name: ")
        account_number = input("Enter the account number: ")
        status = input("Enter the account status: ")

        # Create a dictionary to hold the manually entered account details
        manual_account = {
            "Account name": account_name,
            "Account Number": account_number,
            "Status": status
    }

        divider = "-------------------------------------"
        print(divider)

    # Define a list of available entities
        entities = ["Experian", "TransUnion", "Equifax", "LexisNexus"]

    # Prompt the user to select an entity from the list
        print("Available entities:")
        for i, entity in enumerate(entities, start=1):
            print(f"{i}. {entity}")

        entity_choice = int(input("Enter the number of the entity to which this letter is sent: "))
    
        if 1 <= entity_choice <= len(entities):
            selected_entity = entities[entity_choice - 1]

            divider = "-------------------------------------"
            print(divider)

            # Prompt the user to specify action and issue
            action = input("Enter the action for the letter (e.g., Dispute, Request Information): ")
            issue = input("Enter the issue for the letter (e.g., Incorrect balance, Wrong account status): ")

            divider = "-------------------------------------"
            print(divider)

            # Define a list of available prompt templates
            prompt_templates = [
                "Write a letter {action} for {entity} regarding {issue}. The account is {account_name} with account number {account_number} and has the following account status: {marks}",
                "I need a letter {action} for {entity} about an issue regarding {issue}. The account is {account_name} with account number {account_number} and has the following account status: {marks}",
                "Compose a letter {action} for {entity} about {issue}. The account is {account_name} with account number {account_number} and has the following account status: {marks}",
                "Draft a {action} under 15 U.S Code 1681e(b) - Inaccurate Reporting, for {account_name}, {account_number}. Sending it to {entity} for {issue}",
                "Write a letter to {entity} informing them to ensure the {issue} for {account_name}, {account_number} before I exercise the {action}. I am aware that they will have to pay thousands of dollars in attorney fees, an initial fee of $367 or more, a case management fee of $1,400, and an arbitrator deposit fee of $1,500. They stand to incur many more fee during this process so it is in their best interest to comply or I will exercise my right to arbitration."

            ]

            divider = "-------------------------------------"
            print(divider)

            # Print the available prompt templates
            print("Available prompt templates:")
            for i, template in enumerate(prompt_templates, start=1):
                print(f"{i}. {template}")

            # Prompt the user to select a prompt template by index
            template_choice = int(input("Enter the number of the desired prompt template: "))
            if 1 <= template_choice <= len(prompt_templates):
                selected_template = prompt_templates[template_choice - 1]
            else:
                print("Invalid template choice. Using the default template.")
            # Define a default template
            selected_template = "Default Template: Write a letter {action} for {entity} regarding {issue}. The account is {account_name} with account number {account_number} and has the following account status: {marks}"

        # Fill in the selected prompt template with user input, selected entity, and manual account details
            prompt = selected_template.format(
                action=action,
                entity=selected_entity,
                issue=issue,
                account_name=manual_account["Account name"],
                account_number=manual_account["Account Number"],
                marks=status
            )

        # Generate the dispute letter using the OpenAI API
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                 {
                        "role": "system",
                        "content": "You are DisputeGPT, a bot that creates credit dispute letters. Use your knowledge of UCC, CFPB regulations, and USC to write compelling letters that address inaccuracies and potential infringements by creditors. Be sure to always include the account name and account number within the letter."
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

        else:
            print("Invalid entity choice.")





if __name__ == "__main__":
    main()   






