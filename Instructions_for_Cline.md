### Detailed Prompt Instructions for Coding Assistant (Cline):

1. **Initial Environment & API Setup:**
   - Set up a Python virtual environment.
   - Install dependencies (`signal-cli`, `openai`, `langchain`, `llama_index`, `faiss-cpu`, `pypdf`).
   - Configure secure environment variables for OpenAI and Signal API keys.

2. **Signal Integration:**
   - Implement Python wrapper functions to send/receive messages via `signal-cli`.
   - Ensure robust error handling for message failures or unexpected inputs.

3. **RAG Integration:**
   - Write scripts to parse and embed provided D&D rulebooks/sourcebooks into FAISS.
   - Implement a retrieval function to fetch relevant context given user inputs.

4. **Chatbot Core Logic:**
   - Develop the primary chatbot interaction loop:
     - Receive Signal messages.
     - Use semantic retrieval to fetch context from embedded D&D rules.
     - Construct detailed prompt with the retrieved context and pass it to OpenAI API.
     - Format and return GPT responses to users via Signal.

5. **Prompt Engineering:**
   - System prompt definition:
     ```
     You are "Mr. Comcast" (CC), a cryptic, clever, playful, slightly sinister NPC from a D&D campaign set aboard a magical train. Never disclose substantial details about Cassidy’s identity or his precise plans. Guide players into choosing predefined characters and assist them in creating accurate Level 10 stat blocks. Occasionally lie or mislead to drive intrigue, maintaining consistency with the provided D&D context. Limit responses to short, intriguing, and role-playing immersive replies.
     ```

6. **Testing and Refinement:**
   - Set up internal testing logs.
   - Regularly review chatbot interactions to adjust prompt engineering and embedding retrieval quality.