# Claude in Chrome on a shared computer

For "set up Chrome", "let my agent use my browser", "can my agent open web pages for me?". Walk the person through it
one step at a time, in plain words: they are not technical and never use a terminal. Wait for them to say each step is
done before the next (AskUserQuestion with "Done" and "I'm stuck"); when they are stuck, ask what they see.

What it gives them: their agents on the shared computer open tabs in Chrome **on their own laptop**, read the pages,
and (if they allow it) click and type there, with the logins the person already has in that browser. Nothing is
installed on the shared computer and no password is ever typed there. The agent and the browser find each other
through the person's Claude account, so the browser can be on any computer.

## 0. Is it switched on for this computer?

Only on a shared computer (`/etc/claude-code/CLAUDE.md` exists). Then:

```
grep -q "Claude in Chrome does not work on this computer" /etc/claude-code/CLAUDE.md && echo "not yet"
```

"not yet": the company's agent board does not start sessions with the browser yet. Stop here and say so: "Your agents
can't use your browser on this computer yet. The admin can switch it on; ask them." Do nothing else.

On a computer of their own (no `/etc/claude-code/CLAUDE.md`), none of this applies: Claude in Chrome is set up there
with `/chrome` in Claude Code. Say so and stop.

## 1. Which account the extension needs

The extension must be signed in to the same Claude account and organization as their agents here. Read them (this
reads only their own account's name):

```
python3 -c 'import json,os; a=json.load(open(os.path.expanduser("~/.claude.json"))).get("oauthAccount") or {}; print(a.get("emailAddress"), "|", a.get("organizationName"))'
```

Tell them both: "sign in with <email>; if it asks for an organization, pick <organization>". A different organization
is the most common reason it fails later.

## 2. The extension, on their laptop

In Chrome on their laptop (Microsoft Edge, Brave and Arc work too; Safari and Firefox do not):

1. Open https://chromewebstore.google.com/detail/claude/fcoeoabgfenejglbffodgkkbkcdhcgfn and click **Add to Chrome**,
   then **Add extension**.
2. Pin it: the puzzle-piece icon at the top right of Chrome, then the pin next to **Claude**.
3. Click the Claude icon and **sign in** with the account from step 1.

## 3. Which agent, and how much it may do

Ask with AskUserQuestion:

- Which of their agents should use the browser (their own: the folders in `~/projects/work/`). One at a time is fine;
  each agent is switched on by itself.
- What it may do. **Read pages** (open pages and read them: a safe start) or **Read and act** (also click, type and
  fill in forms, as they would). Recommend reading only, unless they already know the agent must click or type.

## 4. Switch it on, on the board

On their agent board: the gear (Settings) → **Agents** → the agent → in the **Your sessions** column tick **Chrome:
read pages**, and **Chrome: act** too if they chose that. The board says when it applies.

## 5. A fresh session

The browser is loaded only when a session starts. On the board, open the agent and click **Start fresh session…**: the
current conversation goes under Suspended (they can pick it up later), and a new one starts.

## 6. Test it

Chrome must be open on the laptop. They write to the agent, in the new session:

> Open example.com in a new tab, tell me its title, then close the tab.

A tab opens in their Chrome and the agent answers **Example Domain**. If the agent asks which browser to use (they are
signed in to the extension on more than one computer), they pick the laptop. Done.

Then tell them how to use it: "look up this company's website", "check our listing on <site>", "find the order in
<web app> and tell me its status". The agent works in its own tab group, which they can watch; they can stop it at any
time by closing the tab.

## When it does not work

- **"Browser extension is not connected" or "No browsers are connected":** is Chrome open on the laptop? Is the
  extension signed in with the account and organization from step 1? Then turn the extension off and on again
  (`chrome://extensions`, the switch on Claude's card) and ask the agent again.
- **The agent says it has no browser tools:** the tick from step 4 is missing, or the session started before it: do
  step 5 again.
- **An empty list of connected browsers is not proof of anything:** the list can come back empty in a session that can
  still use the browser. Ask the agent to open a page instead.
- **A login page or a CAPTCHA:** the agent stops and asks; they sign in themselves in that tab, then say "continue".
- **It asks before using a site:** that is the extension's per-site permission; they can allow the site.
- **The laptop is asleep or Chrome is closed:** the agent has no browser until it is open again. Night-time work
  cannot use the browser.

## Safety, say it once

- The agent uses their real browser and their logins: whatever it opens, it reads. They can always see the tabs it
  opens.
- Never type a password, a code or a card number into the chat for the agent. When a site asks for one, they type it
  themselves in the browser.
- To switch it off: untick it in Settings and start a fresh session, or remove the extension from Chrome.
