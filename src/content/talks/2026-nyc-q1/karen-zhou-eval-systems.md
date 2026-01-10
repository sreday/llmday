---
title: "Building Evaluation Systems for AI Coding Agents at Scale"
youtube: ""
speakers:
  - name: "Karen Zhou"
    organization: "Anthropic"
    photo: "Karen Zhou.png"
    linkedin: "https://www.linkedin.com/in/karenzhou0/"
    twitter: ""
---

## Abstract

As AI coding assistants move from novelty to daily workflow, how do we know they're actually helping? This talk explores the technical and philosophical challenges of evaluating AI coding agents when they're deployed to millions of users.

Turning user feedback into signal: Users complain, report issues, and express frustration—but this unstructured feedback is gold for improving models. I'll discuss approaches for transforming messy real-world feedback into structured evaluation datasets, including clustering techniques and rubric generation for consistent assessment.

Measuring subtle failures: Some problems are easy to benchmark; others are felt more than measured. "Laziness," overconfidence, and instruction-following failures don't show up in traditional evals. I'll share frameworks for detecting these behavioral issues through automated assessment pipelines.

The evaluation bottleneck: In my experience, evaluation infrastructure often becomes the limiting factor for model improvement. I'll discuss why building robust eval systems deserves as much engineering investment as the models themselves, and what that looks like in practice.

Multi-agent architectures and new challenges: As coding agents become more autonomous and collaborative, evaluation gets harder. How do you assess a swarm? What metrics matter when agents coordinate on complex tasks?

Closing the loop: Connecting evaluation back to training through RL and human feedback pipelines—what works, what doesn't, and where the field is heading.

## Bio

Karen is a member of technical staff at Anthropic, where she works on developing product eval, RL environments, and multi-agent product features for Claude Code. She builds evaluation systems and feedback pipelines that assess how AI models behave in real-world software development scenarios, transforming user feedback into useful evals for model improvement. On the product side, she actively drives the development of swarm and multi-agent functionality that enables multiple AI agents to collaborate on complex software engineering tasks. Prior to Anthropic, Karen worked at Meta on large language models.
