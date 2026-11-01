---
sidebar_position: 4
---

# Runs

A run is an automated design task that works through several phases and pauses for your approval between phases. List your runs, start a new one, then follow it to each decision point.

## Run list

**What you can do here:**

- **Find a run** with the search box (searches the goal text or run ID) and the status filter (running, queued, awaiting approval, completed, failed, rejected, canceled). The newest runs come first.
- **Start a run** with **New design run**.
- **Refresh** the list without reloading the page.
- **Open a run** by selecting its title or choosing **Inspect** to see phases, evidence, and decisions.

If you have no runs yet, you will see a welcome message. If your search matches nothing, clear the filter. If the connection is down, you will see a *Runs could not be loaded* message with a link to Settings.

## Starting a design run

Starting a run is a two-step wizard: **1. Define the intent**, then **2. Review & launch**.

**Step 1 — What are you building?**

1. Choose a **Project** (archived projects are not offered). If you came from a project page, it is already selected.
2. Describe your **Engineering intent**: what you want to build, operating conditions, loads, sizes, budgets, and what a good result looks like. Concrete descriptions get better results than one-liners.
3. Choose an **Engineering workflow**: *Hardware & robotics* for mixed hardware work, *Mechanical design* for mechanical parts.
4. Choose **Review run** to continue (available once you have picked a project and written an intent).

The **Planned lifecycle** panel on the side shows the phases your run will go through. Each phase ends with a human review — that is where you will be asked to approve later.

**Step 2 — Review & launch.** Check the project name, your intent text, the workflow, and how many review steps there are. Choose **Launch design run** to start — you will be taken to the new run. Choose **Edit intent** to go back and change something. If launching fails, check the run list before trying again so you don't create a duplicate.

## Run details

**What you can do here:**

- **See the facts:** run ID, workflow name, and when it was last updated. Use **Refresh** to check for progress.
- **Make a decision** when the *Your review is required* banner appears: **Review approval** to approve and continue, or **Reject run** to stop it. Approving resumes the work; rejecting ends the run but keeps its history. There is also a link to inspect the project's items before you decide.
- **Inspect evidence:** each phase shows its title, status, and a summary, with expandable **Recorded artifacts** for details. If no phases are shown yet, follow the **Open project** link to see what exists so far.
- **Follow the history** (side panel): the run's status changes in order, newest last.
- **Read error messages** when something goes wrong — they appear in their own panel.

If a run cannot be opened, you will see a *Run could not be loaded* message rather than a blank page.
