---
sidebar_position: 9
---

# Agent sessions + Settings

Sessions show what the AI assistants did; Settings connect the dashboard and choose the AI models.

## Agent sessions

The header tells you how many automated workflows are currently running. Below are two columns:

**Left — follow the work:**

- **Active workflow** panel: the current tasks as a step diagram (done in green, running in orange, waiting in grey). Empty when nothing has run yet.
- **Execution log**: timestamped lines showing which assistant did what and whether it succeeded. **Clear** tidies the view only — it does not delete anything.
- **All sessions** list (when sessions exist): which assistant, what task, current status, and when it started. Select any row to open that session's details.

Sessions follow the active project. Start a design run and you can watch the new session appear here as its status changes.

## Session details

**What you can do here:**

- **See the summary** at the top (task name, which assistant, when it started, current status) plus reference cards (session ID, run ID, completion time).
- **Read the timeline**: each step is labelled as a thought, action, decision, observation, error, or result, with a message and time. If anything looks wrong, note the session ID and pass it to your administrator.

## Settings & connection

Open from **Settings & connection** at the bottom of the navigation rail or the account icon in the top bar.

**Connecting to your workspace:**

1. Enter the **Address** and **Port** your administrator gave you. If you were told the dashboard connects on its own, leave both empty.
2. Choose **Test connection** — it safely checks the address and reports whether it works, without changing your current connection.
3. Choose **Save** to switch to the new connection (all pages reload their data). **Reset** returns to the default connection.
4. The **In use** line confirms which address you are actually connected to.

**Choosing AI models (bring your own key):**

1. Pick a **Provider** (the list shows which are already set up, and which one is currently active).
2. If your administrator gave you an **admin token**, enter it when asked (it is sent with the change and never saved).
3. Paste your **provider key** and choose **Save key**, then type or pick a **Model** and choose **Use this provider and model**. To remove a saved key, choose **Remove stored key** and confirm.
4. Your key is only ever sent to secure addresses — the form warns you otherwise. Keys are stored on your workspace server, never in this browser.

**A note on sharing:** anyone who can reach your workspace address can view and change your designs. Your administrator should keep it on a private network rather than the open internet.
