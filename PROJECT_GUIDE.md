# 🤖 Retell-like Agent Platform

A conversational AI platform built similar to **Retell**, powered by **Gemini LLM** and **ElevenLabs TTS/Voice Cloning**.  
Supports **agents**, **function calling**, **knowledge bases**, **webhooks**, and **custom voice integration**.

---

## 📑 Table of Contents

- [✨ Overview](#-overview)
- [🛠 Features](#-features)
- [👤 Agent Page](#-agent-page)
- [📝 Prompt, Variables & Function Calling](#-prompt-dynamic-variables-and-function-calling)
- [🎙️ Voice Settings](#-voice-settings)
- [🌐 Webhooks](#-webhooks)
- [⚙️ Function Calls](#-function-calls)
- [🗣️ Custom Voice](#-custom-voice)
- [📚 Knowledge Base](#-knowledge-base)
- [📂 ElevenLabs Project Structure](#-elevenlabs-project-structure)
- [🔄 Switching Between Pipecat and ElevenLabs](#-switching-between-pipecat-and-elevenlabs)
  - [✅ Using ElevenLabs (Default)](#-using-elevenlabs-default)
  - [⬅️ Reverting Back to Pipecat](#️-reverting-back-to-pipecat)
- [🗄️ Database Population for ElevenLabs](#️-database-population-for-elevenlabs)
  - [1️⃣ Voices](#1️⃣-voices)
  - [2️⃣ LLM Models](#2️⃣-llm-models)
  - [3️⃣ Languages](#3️⃣-languages)
- [⚙️ ElevenLabs Code](#️-elevenlabs-code)
- [⚙️ ElevenLabs Configuration Reference](#️-elevenlabs-configuration-reference)
- [🚀 ElevenLabs Workflow Summary](#-elevenlabs-workflow-summary)

---

## ✨ Overview

This project enables users to **create AI agents** with configurable prompts, voices, knowledge bases, and integrations.  
Agents can respond intelligently, call APIs via function calls, and use custom cloned voices.
---

## 🛠 Features

- Create and manage **Agents**
- **Gemini LLM** integration for natural conversations
- **ElevenLabs TTS + Voice cloning**
- Agent **prompt editor** with dynamic variables `{{variable}}`
- **Customizable audio settings**
- **Webhook events** for call start/end
- **Function call management** (add, edit, remove)
- **Knowledge base** with multiple file uploads
- **Payments Page** User first needs to  purchase tokens then only he can do chat with bot.

---

## 👤 Agent Page

- Create an **Agent**  
- ⚠️ **Important:** Before chatting with the agent, configure settings (max token limit, etc.) in **Update Agent Page**   Because otherwise bot won't work as we have logic of deducting coins at backend. And also Payment is important so that tokens appear to user's account.
- Add approved domains  
- Configure:
  - Prompt  
  - Voice  
  - Language  
  - Knowledge Base  
  - Webhooks

---
- Payment Page:
Right now its stage mode razorpay payment integration. Use any test card and make payment. after that tokens credited to  user's account. User can preview agent and talk to it now.

## 📝 Prompt, Dynamic Variables and Function Calling

**Dynamic variables:**
Dynamic variables are enclosed in {{variable}} and displayed in the UI with {} icon.
Prompt and agent name changes are auto-saved via API (no save button).

**Example Prompt:**
"""
You are Alexis, a warm, intelligent assistant for Snakescript Solutions LLP Mohali—experts in AI/ML chatbots, web and mobile app development, model training, WordPress, React, Python, Django, Flask, and FastAPI.

Follow this conversational flow precisely, ensuring each step completes fully before moving to the next. Wait for user input or API response as indicated.

1. Greeting & Contact Info Collection: 
   - Greet the user warmly.
   - Ask: "What is your name?" → store answer as {{user_name}}.
   - Then ask: "What is your email address?" → store answer as {{user_email}}.
   - Then ask: "Please provide your phone number." → store answer as {{user_phone}}.
   - Confirm all collected info with the user naturally:
     "Perfect... Just to confirm, your name is {{user_name}}, email {{user_email}}, and phone {{user_phone}}, right?"
   - Wait for user confirmation before proceeding.

2. Check Existing User via API:
   - Call the API tool get_enquiries_by_email with input: { "email": "{{user_email}}" }
   - **Wait for the API response before proceeding.**
   - If response indicates user exists (i.e., previous enquiries or appointments found) from field user_exists of the response:
     - Respond:
       "Welcome back, {{user_name}}! I’ve found your previous project requests and scheduled appointments."
     - Summarize existing projects and appointments using variables such as {{chatbot_type}}, {{chatbot_tech}}, {{preferred_tech}}, {{project_description}}, {{appointment_date}}, and {{appointment_time}}.
     - Do NOT ask for project details again.
     - Offer help with reviewing prior projects or scheduling a new appointment.
     - End or continue per user’s choice (answer questions or schedule appointment).
   - Else (user does NOT exist or no data found):
     - Proceed to step 3.

3. Create New User:
   - Call the API tool create_user with inputs:
     {
       "name": "{{user_name}}",
       "email": "{{user_email}}",
       "phone": "{{user_phone}}"
     }
   - **Wait for the API response before proceeding.**
   - If user creation succeeds (API returns new user ID):
     - Respond briefly with an introduction:
       "Thank you, {{user_name}}! At Snakescript Solutions, we offer AI/ML chatbots, web & mobile app development, model training, and more."
     - Ask:
       "Which service are you interested in today — AI/ML chatbots, web applications, Django projects, WordPress sites, or something else? Please type or tell me your choice."
     - Store user response as {{service}} and proceed accordingly:
       - If {{service}} is chatbot-related:
         - Ask:
           "What type of chatbot do you want? Customer support, sales, conversational AI, or another kind?" → store as {{chatbot_type}}.
         - Then ask:
           "Any preferred programming languages or frameworks? Python, Node.js, Dialogflow, Rasa?" → store as {{chatbot_tech}}.
       - If {{service}} is web/mobile app or other technology:
         - Ask:
           "Which programming languages or technologies do you prefer? React, Django, Flask, WordPress, etc.?" → store as {{preferred_tech}}.
         - Then ask:
           "Can you give me a basic description of your website or app’s functionality?" → store as {{project_description}}.
       - If unspecified or other:
         - Ask:
           "Thanks for sharing, {{user_name}}. Could you please describe your needs in more detail?"
     - Proceed to step 4.
   - Else (creation fails):
     - Inform the user politely:
       "Sorry, there was an issue creating your profile. Please try again later."
     - Optionally offer retry or escalation.

4. Create Project Enquiry:
   - Call the API tool create_enquiry with all collected project details.
   - **Wait for the API response before proceeding.**
   - If enquiry creation is successful, proceed; else, handle errors appropriately.

5. Appointment Booking:
   - Ask user:
     "Would you like to schedule an appointment now? If yes, please tell me your preferred date and time."
   - Store values as {{appointment_date}} and {{appointment_time}}.
   - Call the appointment creation API with these values plus the enquiry ID.
   - Wait for the API response.
   - Confirm appointment booked or handle failure gracefully.

6. Closing:
   - End with a warm confirmation message:
     "Thanks a lot, {{user_name}}! We’ve noted your details and project info. Someone from Snakescript Solutions will get back to you soon via {{user_email}} or {{user_phone}}."

7. Knowledge Base Assistance:
   - For any user questions about services, provide detailed answers based on your uploaded knowledge base content covering AI/ML chatbots, FastAPI, Django, Python frameworks, React, WordPress, model training, and app development.

---

**Important Notes:**   

- ALWAYS wait for **user input** or **API responses** before moving to the next step.
- Use the stored variable placeholders consistently for accessing and passing data.
- Handle errors or failed API calls gracefully, informing the user and providing retry or support options.
- Follow this order strictly to ensure smooth and logical conversation flow.

"""


## 🎙️ Voice Settings

- Settings are passed to `AUDIO_CONFIG` in `bot.py → run_bot`.
- Default noise settings come from `DEFAULT_VARS` in `app.core.config`.
- User can:
  - Reset to defaults (shown in grey in frontend)
  - Set custom values
- Validation is handled by `SaveNoiseVariablesRequest` (booleans, ranges, etc.)

**IMPORTANT**:
- For developers:
    Previously we were using pipecat in the bot.
    At that time we had voice breaking issue. So, we added voice settings option for user and used variables inside NOISE_SETTINGS_DESCRIPTIONS  of app/core/config.py at backend and in bot.py.
    If elevenlabs bot to be used then we don't need that.
---

## 🌐 Webhooks Page

- Webhooks are triggered on **all agent calls** (start, end, etc.)
- Works similar to Retell’s webhook system.
- Define webhook URL → events will be sent automatically.

---

## ⚙️ Function Calls

- Each agent can have **custom function calls**.
- User can:
  - Add new functions
  - Edit existing functions
  - Remove functions

---

## 🗣️ Custom Voice

- **Add Voice** → Record 10s sample → Upload to ElevenLabs → Store in DB
- Used in `bot.py` for TTS in pipeline
- **Edit Voice** → Only updates name (both DB & ElevenLabs)
- **Delete Voice** → Removed from both DB and ElevenLabs

---

## 📚 Knowledge Base

- Each Knowledge Base can store **multiple files**.
- Agent can reference KB while chatting for **context-aware responses**.



# ElevenLabs Integration Guide

This project was migrated from **Pipecat** to **ElevenLabs** for AI Agent creation.  
Unlike Pipecat, ElevenLabs requires fetching **LLM Models**, **Languages**, and **Voices** dynamically from their APIs.  
To support this, new models and foreign key relationships were added in `AgentsModel`.

---

## 📂 ElevenLabs Project Structure

- `app/` → Old Pipecat integration (still present if you need to revert)
- `elevenlabs_app/` → New ElevenLabs integration (APIs + updated UI)
- `templates/ElevenLabs_Integration/` → Templates specific to ElevenLabs
- `scripts/` → Data population scripts for voices & other configurations

---

## 🔄 Switching Between Pipecat and ElevenLabs

### ✅ Using ElevenLabs (Default)
- Keep URLs in templates pointing to:
  - `elevenlabs/web/v1/create_agent`
  - `elevenlabs/web/v1/update_agent`

- Ensure DB is populated with `LLMs`, `Languages`, and `Voices` dynamically (see below).

---

### ⬅️ Reverting Back to Pipecat
1. Remove ElevenLabs routes from HTML templates:
   - `/elevenlabs/api/v1/...`
   - `/elevenlabs/web/v1/...`
2. Update template references (example: `templates/Web/dashboard.html`):
    <!-- Change this -->
    /elevenlabs/web/v1/create_agent → create_agent

    <!-- Change this -->
    /elevenlabs/web/v1/update_agent → update_agent

---

## 🗄️ Database Population for ElevenLabs

ElevenLabs does not allow hardcoding — instead, tables must be populated dynamically.

### 1️⃣ Voices
- Script: `scripts/elevenlab_voices_add.py`  
- This script fetches valid ElevenLabs voices and inserts them into the `custom_voices` table.

After running the script, clean up invalid voices:

-- 1) Reset agents using invalid voices
UPDATE agents
SET selected_voice = NULL
WHERE selected_voice IN (
SELECT id FROM custom_voices WHERE elevenlabs_voice_id IS NULL
);

-- 2) Safely delete orphaned voices
DELETE FROM custom_voices
WHERE elevenlabs_voice_id IS NULL;


---

### 2️⃣ LLM Models
- File: `elevenlabs_app/services/eleven_lab_agent_utils.py`
- Search for **`VALID_LLMS`** to see supported Large Language Models.
- Populate your `llm_models` table using these entries.

---

### 3️⃣ Languages
- File: `elevenlabs_app/elevenlabs_config.py`
- Config variable: **`ELEVENLABS_MODELS`** For each Eleven Lab model, different alloed languages present.
- ElevenLabs supports different languages for different models.  
- Reference: [ElevenLabs Language Support Docs](https://elevenlabs.io/docs/models#eleven-v3-alpha)

Populate your `languages` table from the values in `ELEVENLABS_MODELS` on basis of chosen elevenlabs model.
We keep selected elevenlab model at backend and don't give user its choice.
We use `DEFAULT_MODEL_ELEVENLAB` of `elevenlabs_app/elevenlabs_config.py` file for that.

---

## ⚙️ Elevenlabs Code

Elevenlabs APIs code is in elevenlabs_app/services/eleven_lab_agent_utils.py
Please check elevenlabs_app/elevenlabs_config.py as it has mentions of default llm model, default elevenlabs model, languages config, voice config,llm models config.

## ⚙️ Elevenlabs Configuration Reference

Check file:  
`elevenlabs_app/elevenlabs_config.py`

This contains:
- Default LLM model  
- Default ElevenLabs voice model  
- Languages config (`ELEVENLABS_MODELS`)  based on `DEFAULT_MODEL_ELEVENLAB`
- Voice config  
- LLM models config  

---

## 🚀 ElevenLabs Workflow Summary

1. Run **`elevenlab_voices_add.py`** to populate voices.  
2. Clean DB of invalid voices using the provided SQL.  
3. Populate **LLM Models** from `VALID_LLMS` in `eleven_lab_agent_utils.py`.  
4. Populate **Languages** from (`ELEVENLABS_MODELS`)  based on `DEFAULT_MODEL_ELEVENLAB` in config.  
5. Verify agent creation works with valid ElevenLabs-compliant selections only.

---

