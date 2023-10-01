import argparse
import os
import openai
import pandas as pd
from dotenv import load_dotenv
from colorama import Fore, Style


def main():
    load_dotenv()
    openai.api_key = os.getenv("OPENAI_API_KEY")

    parser = argparse.ArgumentParser(description="Dispute GPT")
    parser.add_argument('--pdf_path', type=str, help='The path to the PDF file (required in dispute mode)')
    parser.add_argument('--mode', '-m', choices=['dispute', 'chat'], help='The mode to enter (dispute or chat)')
    


    args, _ = parser.parse_known_args()


    print(f"{Fore.CYAN} Selected mode: {args.mode}")
    divider = "---------------------------------------------------------------"
    print(divider)
    print(f"{Fore.LIGHTWHITE_EX} Welcome to the Dispute GPT!")
    divider = "---------------------------------------------------------------"
    print(divider)
    print(f"{Fore.LIGHTWHITE_EX}Description: This tool is designed to help users generate dispute letters by extracting derogatory accounts from credit profile PDF (Experian, mainly) allowing the user to pick which account they want to dispute, create a custom prompt with a custom action & issue or dispute reason then having GPT 3.5 take care of the letter creation.")  # Replace with your actual instructions
    divider = "---------------------------------------------------------------"
    print(divider)
    print(f"{Fore.LIGHTYELLOW_EX}Usage:")
    print(f"{Fore.LIGHTYELLOW_EX}1. To generate dispute letters, run {Fore.LIGHTRED_EX}'dispute' mode with 'python cli.py --mode dispute --pdf_path /path/to/your/file.pdf")
    print(f"{Fore.LIGHTYELLOW_EX}2. To enter chat mode for interactive conversation, use the -c or --chat option.")
    print("   In chat mode, you can ask questions and get assistance from the AI.")
    print("3. Type 'quit' to exit chat mode at any time.")
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
        if not os.path.isfile(pdf_path):
            print(f"{Fore.LIGHTRED_EX}Error: The specified file '{pdf_path}' does not exist.")
            return

    from test import extract_tables_from_pdf, extract_info_from_dataframe  

    tables = extract_tables_from_pdf(pdf_path)
    dfs = process_tables(tables)

    extracted_info_list = []
    for df in dfs:
        extracted_info = extract_info_from_dataframe(df)
        if any('Status' in info[0] and ('Account charged off' in info[1] or 'Collection account' in info[1]) for info in extracted_info):
            extracted_info_list.append(extracted_info)

def process_tables(tables):
    dfs = []
    for table in tables:
        column_names = [item for sublist in table[:17] for item in sublist]
        df = pd.DataFrame(table[17:], columns=column_names)
        dfs.append(df)
    return dfs
        
     

if __name__ == "__main__":
    main()   











