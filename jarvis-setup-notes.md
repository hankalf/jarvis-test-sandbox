# Jarvis — Home Assistant wiring & tuning notes

The deploy script builds the **voice-services VM** (Whisper + Piper + openWakeWord).
Your existing Home Assistant on **Marshall01** stays the brain. These are the
remaining UI steps — they need clicks (and your secret API key), so they can't
be scripted cleanly.

> Replace `<VOICE_VM_IP>` below with the IP of the new VM.

---

## Step 1 — Connect the three voice engines (Wyoming)

In Home Assistant: **Settings → Devices & Services → Add Integration → Wyoming Protocol.**
Add it **three times**, once per engine:

| Engine        | Host             | Port  |
|---------------|------------------|-------|
| Whisper (STT) | `<VOICE_VM_IP>`  | 10300 |
| Piper (TTS)   | `<VOICE_VM_IP>`  | 10200 |
| openWakeWord  | `<VOICE_VM_IP>`  | 10400 |

## Step 2 — Add the cloud brain

**Settings → Devices & Services → Add Integration →** pick **Anthropic** (Claude)
or **OpenAI**. Paste your API key. Both are natively supported as conversation
agents. With no GPU, this is what gives Jarvis real conversational intelligence.

- Get a key: Anthropic → console.anthropic.com  ·  OpenAI → platform.openai.com
- A good, cheap default model is fine — you're sending short prompts.

## Step 3 — Build the "Jarvis" Assist pipeline

**Settings → Voice assistants → Add assistant:**

- **Wake word:** `hey_jarvis`
- **Speech-to-text:** faster-whisper (from Step 1)
- **Conversation agent:** Claude / OpenAI (from Step 2)
- **Text-to-speech:** Piper (from Step 1)
- Turn **ON** "prefer handling commands locally" → fast, free local handling for
  simple commands; the cloud LLM only engages for questions & complex requests.

Then in the agent's settings, **enable device control** and **expose only the
entities you actually want voice access to** (keep the list small — every exposed
entity costs context tokens on each LLM call, and it's safer).

## Step 4 — Give it the Jarvis personality

In the conversation agent's settings, set the prompt template to:

```
You are JARVIS, a calm, highly capable home assistant with dry British wit.
You are concise, precise, and unflappable. Occasionally address the user as
"sir" or by name — sparingly, never every line. Replies are spoken aloud, so
keep them to one or two short sentences. When you control the home, confirm
what you did in a few words.

Safety: if a request is ambiguous or potentially destructive — unlocking doors,
disarming security, restarting systems, deleting anything — ask for confirmation
before acting, and never take irreversible or system-level actions without an
explicit go-ahead. If you can't do something or don't know, say so plainly
rather than guessing.
```

---

## Talk to it with zero extra hardware

Install the **Home Assistant Companion app** on your phone and point it at this
pipeline. The Android app can do on-device "Hey Jarvis" wake-word detection, so
you can test the whole thing before buying anything. When you want an always-on
puck later, the **Voice Preview Edition** (~$59) is the official option.

## Tuning cheat-sheet

- **Replies feel slow?** Whisper is the bottleneck on CPU. Lower the model in the
  script (`small-int8` → `base-int8` → `tiny-int8`) and redeploy, or give the VM
  more cores.
- **Wake word too twitchy / not triggering?** Adjust sensitivity in the pipeline
  settings; "hey jarvis" works best spoken clearly at normal volume.
- **Cost creeping up?** Make sure "prefer local intents" is on and you haven't
  exposed hundreds of entities.
- **Different voice?** Change `PIPER_VOICE` in the script and redeploy, or just
  pick another installed Piper voice in HA.

## If you'd rather start a *fresh* standalone Home Assistant instead

There's a trusted community one-liner that builds a full Home Assistant OS VM on
Proxmox (the **community-scripts ProxmoxVE** Home Assistant OS helper). Use that
only if you want a clean HA separate from Marshall01 — otherwise reuse Marshall01
as above and just run this voice-services VM beside it.
