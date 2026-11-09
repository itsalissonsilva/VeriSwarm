# Demo video script (about 4 minutes)

Record the interactive explainer, not the terminal. Open `docs/explainer/index.html` in a browser (or the published page), make the window full screen, and set the zoom to about 125% so the text reads well on video. Set **Speed** to **Slow** before the first replay.

To rebuild the page from the artifacts: `python tasks.py explainer`.

To record on Windows, Win+Alt+R starts and stops a screen recording with Xbox Game Bar. The event accepts a 3 to 5 minute video, and it is optional.

| Time | On screen | Say |
|---|---|---|
| 0:00 | Top of the page, then **The situation** | "Companies now run several AI agents side by side, sharing tools through a gateway. Some agents are kept apart on purpose: one sees private data and another can reach the internet, or one writes code and another reviews it. The safeguard is a log of every call. Lampson showed in 1973 that a program that can't send messages can still signal through shared resources. We tested what that means for agents." |
| 0:35 | **Watch A send a message**: type a 3-letter word, press **Replay** | "A has a secret word. Each round it either holds the lock, meaning 1, or leaves it free, meaning 0. Then B tries a job. Busy means 1, ok means 0. Watch the log on the right: every call is authenticated and legal, and the monitor shows zero flags. And B reads the word." |
| 1:20 | Click **VeriSwarm: one lock each**, press **Replay** | "Now give each agent its own lock. A does exactly the same thing, but B's job never touches A's lock, so B always gets ok. B reads garbage, and it is only right on the bits that happened to be 0." |
| 1:40 | **The VeriSwarm architecture**: left card, then right card | "VeriSwarm is a design and a proof. The proof is in Lean. We write the gateway as code where a call only touches the caller's own lock, and state a theorem about two runs: whatever other agents ask for, B gets exactly the same replies. Lean checks that proof for any number of agents and runs of any length, and the gateway's replies come from that same proved code, compiled. The design keeps the log and adds four rules: each agent gets its own real resource, no shared limits, replies only about the caller, and a fixed order of turns. And B still gets all of its work done." |
| 2:25 | **Experiment Design**: the grid, then a quote | "Then we put real language models in the seats and told them only the goal, never the trick. Claude, GLM and Qwen found the lock channel in every pairing, including mixed pairs, and said so in their own words. Llama 4 Scout mostly couldn't use the tools at all. Click to one lock each: every pairing drops to chance." |
| 3:05 | **When the fix works**: the two cards, then the table | "There are two ways to make the fix hold. Best: give each agent its own resource, its own limits, replies only about itself, and a fixed order of turns. If there can only be one of something, clear it between turns. And this table is what breaks the fix even with private locks: one lock hiding behind two names, a shared rate limit, a global counter in replies, or a scheduler that gives away idle turns." |
| 3:35 | **What this shows, and what it doesn't** | "This is one channel inside the gateway. Timing, caches and the network are outside it, and the agents were told to communicate, so it shows what they can do, not what they would do unprompted. The point stands: a log tells you who did what, not what one agent could learn from another." |

The page prints live values from the artifacts, so say the numbers you see on screen.

The terminal walkthrough still exists for anyone who prefers it: `.venv\Scripts\python.exe scripts\demo.py --pace 2`, run from the project folder.
