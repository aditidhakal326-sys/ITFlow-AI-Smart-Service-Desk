ITFlow AI — Smart Service Desk
An AI-powered IT support ticket management application built with Python, Streamlit, SQLite, and Ollama.
ITFlow AI uses a locally running large language model (LLM) to classify IT support requests, recommend ticket priorities, and assign requests to appropriate IT support teams. Human approval is required before tickets are saved and routed.
Features
•	AI-Powered Classification: Uses Ollama's qwen3:0.6b model to classify support tickets by category and priority.
•	Human-in-the-Loop Review: Allows users to confirm or correct AI recommendations before saving.
•	Ticket Management: Search, filter, update, and resolve support tickets.
•	SQLite Database: Stores tickets, approval history, corrections, and routing information.
•	Ticket Routing Queue: Automatically queues approved tickets for processing.
•	Local Delivery Processor: Delivers Pending tickets into persistent SQLite team inboxes.
•	Team Inbox: Displays tickets assigned to specific support teams.
•	Ticket Acknowledgment: Allows a team member to acknowledge a delivered ticket and move it to In Progress.
•	Resolution Tracking: Records resolution notes and delivery activity.
•	Analytics Dashboard: Displays ticket distribution, priorities, assignments, and resolution progress.
•	AI Evaluation: Tests classification on five predefined sample cases.
Technology Stack
Technology	Purpose
Python	Application logic
Streamlit	Web interface
SQLite	Local database
Ollama	Local LLM execution
Qwen3 0.6B	AI ticket classification
HTML/CSS	Interface styling
urllib	Communication with the local Ollama API
Requirements
•	Python 3.13 (or a compatible version)
•	Streamlit
•	Ollama
•	Ollama model: qwen3:0.6b
Installation
1.	Download or clone this repository.
2.	Open the project folder in VS Code.
3.	Install the required Python packages:
py -3.13 -m pip install -r requirements.txt
4.	Download the Ollama model:
ollama pull qwen3:0.6b
5.	Ensure Ollama is running.
6.	Start the application:
py -3.13 -m streamlit run main.py
7.	Open the local URL displayed in the terminal.
How It Works
1.	An employee submits an IT support issue.
2.	The local AI model recommends a category and priority.
3.	A human reviews and approves the recommendation.
4.	The ticket is saved to SQLite and added to the routing queue.
5.	The local delivery processor places the ticket in its assigned team's inbox.
6.	A team member acknowledges the ticket.
7.	The ticket moves to In Progress.
8.	The issue is resolved and a resolution note is recorded.
9.	The application updates its ticket records and analytics.
Example Workflow
Submitted issue: My laptop screen is broken, and I cannot use my computer.
AI classification: Hardware Issue
Priority: Medium
Assigned team: IT Hardware
Routing status: Pending → Routed
Ticket status: Open → In Progress → Resolved
Data Storage
The application uses a local SQLite database named tickets.db.
The database is automatically created when the application starts. It stores ticket details, routing requests, team inbox deliveries, and activity records.
The database is excluded from GitHub using .gitignore to prevent publishing private ticket data.
Project Limitations
•	The application is a local prototype, not a production service desk.
•	Ticket delivery occurs inside SQLite; no external email or notifications are sent.
•	Team inbox selection does not provide authenticated user access.
•	The delivery processor must be triggered manually.
•	Manually changing a routing status to Routed does not constitute actual local delivery.
•	AI accuracy testing uses five predefined examples and does not establish real-world model reliability.
Future Improvements
•	ServiceNow or Jira integration
•	Microsoft Teams or email notifications
•	Role-based user authentication
•	Automatic background ticket processing
•	SLA monitoring and escalation
•	Larger AI evaluation datasets
Project Type
Personal Portfolio Project — AI-Powered IT Support Automation
Developed to demonstrate practical skills in Python programming, local AI integration, SQL databases, ticket management workflows, and dashboard development.

Project Files and Required Packages
The following files are used to run, document, and manage the ITFlow AI application.
File	Description
main.py	Contains the main Python code for AI classification, ticket management, routing, and analytics.
README	Explains the project's features, setup instructions, and functionality.
requirements.txt	Lists the required Python packages. The application requires streamlit>=1.32,<2.
.gitignore	Tells Git which files and folders should not be tracked or uploaded to the repository, including the local ticket database, cache files, and virtual environments.
tickets.db	Stores ticket information, routing records, and resolution history locally. This file is excluded from GitHub.
Required Package
The application's required external Python package is Streamlit, specified in requirements.txt as streamlit>=1.32,<2.
Other modules, including sqlite3, json, datetime, and urllib, are provided by Python. Ollama and the qwen3:0.6b AI model are installed separately.


