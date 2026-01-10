---
title: "Stop Making Your Agents More Expensive, Make Your Retrieval Better"
youtube: ""
speakers:
  - name: "Jakub Rohleder"
    organization: "Monday.com"
    photo: "Jakub Rohleder.png"
    linkedin: "https://www.linkedin.com/in/jakub-rohleder-35a0b16b/"
    twitter: ""
---

## Abstract

Everyone is racing to build stronger AI agents. But a harder question is how to make them economically viable at scale. The industry tends to focus on adding more capabilities and bigger models, while costs quietly explode across dimensions most teams do not think about early on.

In practice, many teams start with an LLM plus tools, keep layering on features, and end up with systems that do not scale economically. Poor architectural decisions and missing optimizations compound across database operations, embedding computation, and agent inference.

This talk explores the economics of architecture choices when building production AI agents. Drawing from experience building semantic search for billions of entities at Monday.com and integrating it into AI-driven products, it covers concrete architecture lessons that help control costs. Topics include using deterministic systems for predictable operations, building vector search for retrieval, and reserving LLM reasoning for cases that truly need it.

Attendees will see real production examples, cost breakdowns, and decision frameworks for choosing the right tool for each problem. You do not have to choose between capability and cost, but you do need to be intentional about where you invest in reasoning versus where you rely on retrieval. These trade-offs become critical when scaling from prototype to product.

## Bio

Kuba Rohleder is a Staff Software Engineer at Monday.com, where he builds hybrid search systems for AI agents at massive scale, including billions of vectors and hundreds of millions of daily updates. With a background in large-scale systems and experience leading the GCP Kubernetes UI at Google, he now focuses on making AI architectures economically sustainable.

His approach emphasizes retrieval as the core, guardrails for predictable flows, and tiered AI and ML models as composable building blocks. Outside of work, he is a prolific builder, creating everything from nutrition tracking apps to smart home voice control systems, including training his own trigger word model from scratch.
