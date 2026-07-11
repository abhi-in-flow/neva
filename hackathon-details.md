# Google DeepMind Bangalore Hackathon
*Participant Guide — Event Details*

This guide is your all-in-one resource for the event, including schedule, rules, technical resources, problem statements, judging information, and more. Please read this carefully; most answers can be found here.

---

## 1. Your Goal: Hackathon Problem Statements

You are required to build in one of our four hackathon problem statements! We want to see something that's never been built before utilizing the latest from the Google AI Stack:

- Gemini 3.5 Flash (`gemini-3.5-flash`) — including [Computer Use](https://ai.google.dev/gemini-api/docs/computer-use#safety-best-practices)
- Gemini Audio
- Gemini Live Translate (`gemini-3.5-live-translate-preview`)
- Gemini Flash Live (`gemini-3.1-flash-live-preview`)
- Gemini 3.1 Flash Text-to-Speech (`gemini-3.1-flash-tts-preview`)
- GenMedia — Omni (`gemini-omni-flash-preview`)
- Nano Banana 2 Lite (`gemini-3.1-flash-lite-image`)
- Interactions API (`antigravity-preview-05-2026`)

### Problem Statement 1: Real-Time Multimodal Interaction
**Focus Technology:** Gemini Live API / Live Translate

**The Challenge:** Most "voice assistants" are just text interfaces wearing a microphone—wait, process, respond, repeat. The Live API breaks that rigid turn-based structure: users can interrupt mid-response, the model reads vocal tone in real time, and it can see what the user sees through a live camera feed. Once a model can see and hear continuously, it must make decisions a traditional chat interface never has to. How does the interface handle live interruptions? What should it do when it notices something in the video feed that the user hasn't explicitly pointed out? How can Live Translate break down language barriers instantly without feeling like a sluggish translator app?

**The Bar:** Build around the real-time, multimodal interaction paradigm itself, not a screen with a voice layer stapled on. If your application works just as well typed into a chatbox, you aren't leveraging the Live API's true potential.

### Problem Statement 2: Autonomous Orchestration with Managed Agents (iAPI)
**Focus Technology:** iAPI & Managed Agents (Antigravity)

**The Challenge:** True enterprise and functional automation require orchestration that spans multiple APIs, databases, and environments. Utilizing our iAPI framework and Managed Agents (Antigravity), we want you to build complex, multi-agent systems that can plan, delegate, and execute multi-step workflows. How do multiple agents hand off tasks to one another without losing context? How do they safely interact with external tools and APIs while maintaining security and predictability? Build an agentic system that tackles complex, open-ended objectives (e.g., automated coding pipelines, comprehensive market research, dynamic logistical planning) where a single agent would fail.

**The Bar:** We want to see genuine collaboration. Show us how your agents split labor, resolve conflicts, and use tools to achieve a larger goal without constant human hand-holding.

### Problem Statement 3: High-Throughput Creative Workflows with NB2 Lite
**Focus Technology:** Nano Banana 2 Lite

**The Challenge:** Traditional AI image generation is slow (10+ seconds) and computationally expensive, which prevents it from being used in dynamic, real-time programmatic pipelines. Nano Banana 2 Lite (NB2 Lite) flips this paradigm on its head, delivering 1K resolution image generation and high-fidelity text rendering in under 4 seconds at a fraction of the cost ($0.034 per 1,000 images). Your challenge is to build high-velocity, automated asset pipelines, localized typographic engines, or real-time personalized UI/UX generators. How do you scale visual generation when cost and latency are no longer bottlenecks?

**The Bar:** If your app is just a standard prompt-box-to-image generator, it's not leveraging the speed of NB2 Lite. Show us automated, programmatic pipelines, dynamic ad localizers, or interactive storytelling canvases where real-time, high-volume generation is load-bearing to the user experience.

### Problem Statement 4: Conversational Video & Motion with Omni Flash
**Focus Technology:** Gemini Omni Flash (`gemini-omni-flash-preview`)

**The Challenge:** Video generation has historically been a black box—you type a prompt, wait, get a clip, and if it's slightly off, you have to start from scratch. Gemini Omni Flash bridges the gap between reasoning and creation, allowing users to not only generate video from any combination of inputs (text, images, audio, video) but also edit and iterate on those videos through continuous, conversational natural language. We want to see applications that redefine how humans direct and manipulate motion. How can users swap elements, apply motion transfers, or build consistent multi-shot narrative timelines through a conversational interface?

**The Bar:** We are looking for genuine multi-turn conversational video orchestration, style transfers, or element swapping that respects physical world dynamics (gravity, lighting, perspective). We highly encourage pipelines that chain NB2 Lite (for ultra-fast image generation) into Omni Flash (for conversational video animation and editing).

### Special Prize: Best Use of Gemma 4 — Local-First Agents on Gemma
**Focus Technology:** Gemma On-Device (Gemma 4 E2B & E4B)

**The Challenge:** Most "on-device AI" just moves a chatbot from the cloud onto the phone—same single-turn shape, same assumption that a reliable signal will eventually come back. Running locally isn't acting autonomously. Real agency means holding state across a task, deciding what to do next based on what's already been learned, and recovering when the plan breaks—entirely offline. Across regions with spotty connectivity, high data costs, or for sensitive tasks in healthcare, finance, and personal privacy, sending data to a server is a non-starter. Build the full sense-decide-act-check loop, running entirely on-device on Gemma.

**The Bar:** If you can draw your agent as a single, straight arrow from input to output, it isn't an agent yet. Show us local error recovery, local state management, and clear boundaries for when the agent defers to a human.

---

## 2. Getting Ready

**Location:** WeWork Roshni Tech Hub (Ground floor, PFS Club House, Roshni Tech Hub, Marathahalli Main Rd, Lakshminarayana Pura, EPIP Zone, Chinnapanna Halli, Bengaluru, Karnataka 560037, India)

**Arrival:** Doors open at 9:00 AM IST.

### Wi-Fi Access
| Username | Password |
|---|---|
| `cv x gdm` | `hackathon` |
| `CV x GDM` | `hackathon` |

### Parking (Paid)
- ₹70/day for two-wheelers
- ₹250/day for four-wheelers

---

## 3. Connect with the Community

Join the Hackathon Discord to meet other participants, get official updates, and begin forming teams: https://discord.gg/ajZyFNGcd

**Getting Started:**
- **Introduce yourself:** In `#intros`, share who you are, the skills you bring, and what project you're looking to build.
- **Create a team:** In `#team-search`, find teammates before the hackathon (maximum team size of four).

**Key Channels:**
- `#general` — Socialize and meet other hackers.
- `#rules` — On the day rules spanning registration, product building, and pitching.
- `#announcements` — Official updates and reminders from the CV Team.
- `#intros` — Introduce yourself and what you're doing to everyone!
- `#team-search` — Find teammates before the hackathon (maximum team size of four).
- `#questions` — Ask the CV Team general questions by pinging @CV, or Google DeepMind questions by pinging @Google DeepMind.

Tag @googleaidevs / #BuildWithGemini to share your builds.

---

## 4. Schedule Overview

| Time | Event |
|---|---|
| 9:00 AM | Doors open, breakfast provided, team formation |
| 10:00 AM | Welcome kick-off |
| 10:30 AM | Hackathon begins |
| 1:00 PM | Lunch served |
| 5:00 PM | Submissions due |
| 5:00 – 6:45 PM | First round judging |
| 6:00 PM | Dinner served |
| 7:00 – 8:00 PM | Final round judging |
| 8:15 PM | Winners announced |
| 10:00 PM | Doors close |

---

## 5. Hackathon Rules

- **Open Source:** Repositories must be public.
- **Team Size:** A maximum of four team members per team. Solo participants are allowed.
- **Demo Requirements:** Your demo must only highlight the specific features, code, and functionality that your team built during the hackathon. Judges must be able to clearly identify what was created during the event. Failure to clearly identify your original contributions will result in immediate disqualification.
- **New Work Only:** You may not present an existing project as your own work. Failure to clearly distinguish your contributions will result in immediate disqualification.
- **Banned Projects:** Projects will be disqualified if they violate legal, ethical, or platform policies, or use code, data, or assets you do not have the rights to.

### 🚫 Sample Anti-Projects to NOT Do — Strictly No
- AI Mental Health Advisor
- Basic RAG Applications
- Streamlit Applications
- Image Analyzers
- "AI for Education" Chatbot
- AI Job Application Screener
- AI Nutrition Coach
- Personality Analyzers
- Any project using AI to generate and give medical advice
- Any project where a dashboard is the main feature
- Sports analyzers or coaches

---

## 6. Google Provided Resources

Temporary accounts will be provisioned the day of the hackathon.

- **Gemini 3.5 Flash:** https://ai.google.dev/gemini-api/docs/interactions/whats-new-gemini-3.5
- **Gemini API Quickstart:** https://ai.google.dev/gemini-api/docs/quickstart
- **Gemini API Cookbook:** https://github.com/google-gemini/cookbook
- **Gemini API Docs:** https://ai.google.dev/gemini-api/docs
- **Managed Agents — Docs:** https://ai.google.dev/gemini-api/docs/agents
- **Managed Agents — Blog:** https://blog.google/innovation-and-ai/technology/developers-tools/managed-agents-gemini-api/
- **Managed Agents — Video:** https://www.youtube.com/watch?v=OdrOmc_RX8A

---

## 7. Submission Process

Teams should submit at https://cerebralvalley.ai/e/google-deepmind-bangalore-hackathon/hackathon/submit when they have completed hacking. In the submission form, you will have to submit a short one-minute demo video. This should be a video highlighting the specific features, code, and functionality that your team built during the hackathon.

Please double check that your repository is public, your demo link is accessible, and all team members have been added to the submission page.

---

## 8. Judging Process

Judging will take place in two rounds.

### Round One
Hackers will be assigned to judging groups in different rooms of the venue. Each team will have ~3 minutes to live demo their project, followed by 1-2 minutes of Q&A. The following criteria will be used:

| Criterion | Weight | What's Assessed |
|---|---|---|
| Creativity and Originality | 35% | How creative is the project? Is this something you have never seen before? |
| Live Demo | 25% | How is the actual demo of the project? Does it impress you? Is it well-engineered and working? |
| Impact in India | 25% | What is the project's long-term potential for success in India specifically? Will this have a long-lasting impact on the country or any specific local applications? |
| Technical Depth | 15% | How difficult is this project to recreate? Does this involve technicality beyond your average hackathon project? |

### Round Two
The top six teams will demo on stage in front of a panel of judges and all attendees. Each team will have ~3 minutes to live demo their project, followed by 1-2 minutes of Q&A. The same criteria as above will be used, though with **equal weighting** for each category.

---

## 9. Prizes

### Grand Prizes — Best Overall (Top 3)
- 🥇 1st Place: **$5,000** cash prize
- 🥈 2nd Place: **$2,000** cash prize
- 🥉 3rd Place: **$1,000** cash prize

### Special Prize — Best Use of Gemma 4 (Local-First Agents on Gemma)
- Prize: **$2,000** cash prize
