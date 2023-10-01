# Dispute-GPT
Dispute GPT is a Python script designed to streamline the process of handling credit report discrepancies and generating dispute letters. 

# __Installation__ 
Clone this repository to your local machine by copying the code:
```shell
git clone https://github.com/itsbergomy/Dispute-GPT.git
```
Navigate to the project directory via terminal/command prompt:
```shell
cd dispute-gpt
```
Install the required Python packages:
```shell
pip install -r requirements.txt
```

## __Configuration__
Before using Dispute-GPT, you need to set up your OpenAI API key. Follow these steps:
1. Go to [OpenAI] (www.openai.com)
2. Click Menu, which should be in the top right corner
3. Click on API, then click on Overview. The page should open with a blue banner click the 'Get Started' button.
4. You'll then be tasked with creating your own account with a Username and Password.
5. Once your account is made you'll see Personal somewhere at the top of the page, click it then click 'View API Key'.
6. Click create a new secret key, then Vwala! If you've never done this, you created your first API key. Congrats

# __Video Setup__
If you are more of a visual learner, here's an instructional video:
https://www.loom.com/share/be96e1e0e9054d44af16d5f3ec62206c?sid=f6be7426-444a-4dc6-b1ee-2160b9242640

# __Usage__
## Dispute Mode
To generate dispute letters based on a __Experian__ PDF file, use the following command:
```shell
python cli.py --mode dispute --pdf_path /path/to/your/file.pdf
```
Replace '/path/to/your/file.pdf' with the actual path to the PDF file you want to process. This mode extracts information from the PDF and generates  dispute letters.

# __Chat Mode__
To enter interactive chat mode with the AI assistant, use the following command:
```shell
python cli.py --mode chat
```
In chat mode, you can have a conversation with the AI assistant by typing messages as the "User." Type 'quit' to exit chat mode.

# __Contributing__
We welcome contributions to Dispute-GPT. If you find any bugs, have feature suggestions/requests, or want to contribute code please create an issue or submit a pull request.

# __License__
This project is licensed under the MIT License - see the LICENSE file for details.

Please customize this documentation to fit your specific project and requirements. You can include additional sections or information as needed.




