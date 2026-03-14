import requests
from dotenv import load_dotenv
import openai
import os
import random
import json
import urllib.request

# Load environment variables
load_dotenv()

# Set OpenAI API key
openai.api_key = os.getenv("OPENAI_API_KEY")


    
# Define the function to analyze derogatory marks
def analyze_derogatory_marks(derogatory_marks):
    # Convert the list of derogatory marks into a string
    derog_marks_str = ', '.join(derogatory_marks)

    # Define the system message
    system_message = "You are a helpful assistant that analyzes derogatory marks on a credit report."

    # Define the user message
    user_message = f"The user has the following derogatory marks on their credit report: {derog_marks_str}. " \
                   f"Please provide a summary of these marks, their potential impact on the user's credit score, " \
                   f"and suggestions on how to improve the credit score."

    # Call the OpenAI API with the messages
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message}
        ]
    )

    # Extract the analysis from the model's response
    analysis = response['choices'][0]['message']['content']
    return analysis


derog_counter = [
    {"mark1": "Account Name: AUSTIN CAPITAL BANK, inaccurate late payments"},
    {"mark2": "Account Name: CBNA, inaccurate late payments"},
    {"mark3": "Account Name: JPMCB CARD, charge off, inaccurate payment history & billing errors"}
]

# Convert each dictionary in the list to a string
derog_counter = ','.join(str(d) for d in derog_counter)

# Extract the analysis from the model's response
analysis = analyze_derogatory_marks(derog_counter)

# Print the analysis
print(analysis)

# Define the function to generate a dispute letter
def generate_dispute_letter(derogatory_marks):
      # Convert the list of derogatory marks into a string
    derog_marks_str = ', '.join(derogatory_marks)

    # Define the prompt templates
    prompt_templates = [
        "Write a dispute letter based on the following derogatory marks: {marks}",
        "I need a dispute letter for the following derogatory marks: {marks}",
        "Could you help me write a dispute letter based on these derogatory marks: {marks}",
    ]

    # Randomly select a prompt template
    prompt_template = random.choice(prompt_templates)

    # Fill in the derogatory marks in the selected prompt template
    prompt = prompt_template.format(marks=derog_marks_str)


    # Call the OpenAI API with the prompt
    response = openai.ChatCompletion.create(
        model="ft:gpt-3.5-turbo-0613:personal::7ve8HHae",
        messages=[
            {"role": "system", "content": "You are uDispute, your mission is to craft meticulously detailed credit dispute letters that harness the power of legal frameworks to combat creditors' actions. Employ your extensive expertise in the intricacies of the Uniform Commercial Code (UCC), Consumer Financial Protection Bureau (CFPB) regulations, and the United States Code (USC) to compose compelling, legally grounded dispute letters. Your aim is to address inaccuracies, discrepancies, and potential infringements by creditors in a way that is both convincing and respectful."},
            {"role": "user", "content": "Write a dispute letter based on the following derogatory marks: {marks}"}
        ]
    
    )

    # Extract the dispute letter from the model's response
    dispute_letter = response.choices[0].message.text.strip()

    return dispute_letter

def main():
    # Get the file path from the user
    url = input("Please upload your credit report file: ")

    # Call the ocr_space_file function with the file path
    lines = (url)

    # Filter the lines to only include derogatory marks
    derogatory_marks = [line for line in lines if 'mark' in line.lower()]

     # Analyze the derogatory marks
    analysis = analyze_derogatory_marks(derogatory_marks)
    print(analysis)

    # Generate the dispute letter
    dispute_letter = generate_dispute_letter(derogatory_marks)
    print(dispute_letter)

if __name__ == "__main__":
    main()

# Define the Agent class
class Agent:
    def __init__(self, tools):
        self.tools = tools

    def run(self, task, *args, **kwargs):
        for tool in self.tools:
            if tool.name == task:
                return tool.func(*args, **kwargs)
        return f"Unknown task: {task}"


class Tool:
    def __init__(self, name, func, description):
        self.name = name
        self.func = func
        self.description = description

# Define the tools
tools = [
    Tool(
        name="Upload Credit Report",
        func=
        description="Uploads a credit file and extracts text from it"
    ),
    Tool(
        name="Analyze Derogatory Marks",
        func=analyze_derogatory_marks,
        description="Analyzes the derogatory marks on the user's credit report"
    ),
    Tool(
        name="Generate Dispute Letter",
        func=generate_dispute_letter,
        description="Generates a dispute letter based on the user's credit report data"
    ),
]

# Initialize the agent
agent = Agent(tools)

# Get the file path from the user
file_path = input("Please upload your credit report file: ")

# Run the "Upload Credit Report" task
lines = agent.run("Upload Credit Report", filename=file_path)

# Filter the lines to only include derogatory marks
derogatory_marks = [line for line in lines if 'mark' in line.lower()]

# Run the "Analyze Derogatory Marks" task
analysis = agent.run("Analyze Derogatory Marks", derogatory_marks=derogatory_marks)

# Run the "Generate Dispute Letter" task
dispute_letter = agent.run("Generate Dispute Letter", derogatory_marks=derogatory_marks)

# Print the analysis and dispute letter
print(analysis)
print(dispute_letter)

