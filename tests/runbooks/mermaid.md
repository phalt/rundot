---
title: Mermaid example
description: Diagrams in mermaid fences, which rundot web draws and rundot run shows as code.
---

# Mermaid example

A flowchart:

```mermaid
flowchart LR
  A[Write the runbook] --> B{Does it run?}
  B -- yes --> C[Ship it]
  B -- no --> D[Fix it]
  D --> B
  style D fill:#7c2d12,color:#fff
```

A sequence diagram:

```mermaid
sequenceDiagram
  participant You
  participant rundot
  You->>rundot: rundot web hello.md
  rundot-->>You: the page, with a ▶ per block
  You->>rundot: ▶
  rundot-->>You: output, under the block
```

A Gantt chart:

```mermaid
gantt
  title Release
  dateFormat YYYY-MM-DD
  section Build
  Tests        :done, t1, 2026-11-01, 2d
  Package      :crit, p1, after t1, 1d
  section Ship
  Publish      :after p1, 1d
```

This one is broken, so it stays as code with Pele's error under it:

```mermaid
flowchart LR
  A -->
```

## Say it

```sh run
echo "diagrams drawn"
```
