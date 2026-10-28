# A new agent on a shared computer

For "make me an agent for …", "create a new worker", "I need an assistant that …". Only on a shared computer
(`/etc/claude-code/CLAUDE.md` exists); elsewhere the person makes it from the board (Cmd+K, Create a new worker…).
Go one step at a time, in plain words: the person is not technical and never uses a terminal.

## 1. Ask what it is (AskUserQuestion where there are choices)

- **What it is for**, in one sentence ("keeps our price sheets in Google Sheets up to date"), then the details: what it
  does, what it knows, which data is its own, which tools or websites it uses.
- **What it must never do** (send anything without asking, change prices, delete rows, …).
- **Which of their other agents it works with**, and for what (who it asks, who will ask it).
- **Claude or ChatGPT.** Claude unless they ask for ChatGPT; offer ChatGPT only where it is installed
  (`test -x /usr/local/bin/codex`).
- **Its name:** short, lower case, letters, digits and `_` (`sheets_assistant`); it says what the agent does.
  Propose one, let them change it.

## 2. Make it with the board, never by hand

```
board-cli new-agent <name>          # Claude
board-cli new-agent <name> --gpt    # ChatGPT
```

The board makes `~/projects/work/<name>`, marks it trusted for Claude Code and starts its first session, so it is on
their board at once. Never `mkdir` an agent's folder yourself: a folder made by hand is not trusted, and a session the
board starts there stops at Claude's trust question where nobody can answer it (it looks like loading for ever).
If it says the name exists, ask for another name; never reuse or overwrite a folder.

## 3. Write its instructions

**Claude:** create `~/projects/work/<name>/CLAUDE.md` from the template below. **ChatGPT:** the board already wrote
`AGENTS.md` from GPT's kit; replace only its "What you are for" section (keep everything else, it is how GPT works
here), with the same content as the template's first five sections.

The first line under the title matters most: it is what the other agents see when they look for whom to ask (the
ask-team skill lists every agent with that line). Make it one specific sentence, starting with what it is in charge of.

```markdown
# <name>

In charge of <what, for whom, in one sentence>.

## What you do
<the jobs, as the person described them; what a good result looks like>

## What you know and where it is
<the sources of truth: files in this folder, /srv/company/<…>, which connectors or websites; what to read first>

## Working with the other agents
<who to ask for what (ask-team), and who may ask you for what. Facts another agent owns come from that agent.>

## What you may change, and what you never do
<what it may change on its own; everything else asks the person first; the hard limits they named>

## Files
Files for <person> go in `outputs/` in this folder, clearly named. When they want one on their laptop, also copy it
into `~/bridge_folder` (that asks them first).

## Keys and passwords
<only if it uses a service with a key: which service, and that its scripts read the key from `.env` in this folder>
Never ask for a key, password or token in the chat, and never write one into a file yourself. A key goes into this
folder's `.env` through the admin (company keys), never through you; you cannot open `.env` files, by design.
```

What not to write there: where things are on this computer, the bridge folder, the inbox, the safety rules, how to
reply. Every session already gets those from the company's instructions; a copy goes stale. Never write a key, a
password, a customer's personal data or anything private into these files: they are backed up to GitHub.

Show them the file in plain words ("this is what it will know about itself") and change it until they are happy.

## 4. A fresh session, then a test

The first session started before the instructions existed, so it does not know them: on the board, open the agent and
click **Start fresh session…** (ChatGPT: its card's moon, then New session in it). Then they ask it, in the new session:

> What are you for, and who would you ask about <something another agent owns>?

It should answer from its instructions. If it does not, the file is not where it should be: check step 3.

## 5. Back it up

Ask whether to back it up (it is their work): `./setup.sh add ~/projects/work/<name>` (that also keeps `.env` and other
key files out of git), then `./nightly.sh --no-slack <name>` for the first backup (see "Back up" in CLAUDE.md).

## 6. Tell them

It is on their board as `<name>`: they open it there or from Cmd+K. Their other agents can ask it now. To change what
it does later, they tell it ("add to your instructions: …") or ask you.
