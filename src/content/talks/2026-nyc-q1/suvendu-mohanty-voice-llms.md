---
title: "Fine-Tuning LLMs for Voice-First Consumer Experiences"
youtube: ""
speakers:
  - name: "Suvendu Mohanty"
    organization: "Amazon"
    photo: "Suvendu Mohanty.png"
    linkedin: "https://www.linkedin.com/in/suvenduml/"
    twitter: ""
---

## Abstract

Voice-first consumer devices—smart speakers, wearables, in-home assistants, and connected appliances—demand language models that are not only accurate, but fast, reliable, and deeply aligned with user expectations in real-world environments. This talk explores practical methods for fine-tuning large language models (LLMs) to deliver high-quality, low-friction voice interactions at scale.

I will outline a structured approach to supervised fine-tuning (SFT) for voice-driven use cases, including dataset design strategies that capture conversational latency patterns, prosody-driven intent, and the ambiguities inherent in spoken queries. The session will also address real evaluation challenges: how to measure dialog quality, naturalness, grounding, and long-horizon task execution when traditional text benchmarks fall short. Particular attention will be given to diagnosing and reducing hallucinations in consumer contexts, where incorrect answers can undermine trust, cause user frustration, or trigger unintended physical-world actions.

Attendees will leave with a clear picture of what it takes to adapt an LLM into a dependable, voice-native system—from fine-tuning workflows and guardrail construction to continuous evaluation loops informed by real user behavior. This talk aims to provide a practical roadmap for teams building the next generation of on-device and cloud-connected voice experiences.

## Bio

Suvendu is a Senior Machine Learning Engineer on Amazon's Devices Org, where he leads supervised fine-tuning and RLHF pipelines for 7B–470B-parameter LLMs. Over 14 years, he has designed large-scale distributed-training systems with Megatron-LM 3-D parallelism, DeepSpeed ZeRO, PyTorch FSDP, and AWS Trainium, consistently cutting training cost and latency without sacrificing model quality. His MLOps expertise spans SageMaker, MLflow, and on-device TensorRT inference, driving 3× throughput and 30% latency reductions for production workloads. Previously, Suvendu built high-volume data lakes and real-time recommendation engines at HBO Max and architected predictive-maintenance ML platforms at Equinix. He is an active open-source contributor—author of the MLOps framework on AWS's GitHub—and a frequent mentor on distributed-ML best practices. Suvendu holds a master's degree in Computer Science, has presented at internal AWS tech talks, and enjoys demystifying cloud economics for ML practitioners.
