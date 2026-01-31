# ASIN (Assessment of Spatial Intelligence for Navigation)

**ASIN** is a *green-agent benchmark* for **AgentBeats / AgentX** that evaluates a *purple agent’s* ability to perform real-world navigation in **Manhattan, NYC** using two complementary visual modalities:

- A **static 2D route map**
- A **first-person Street View image**

Each episode starts at **Point A** and asks the purple agent to navigate to the **final red marker** under a **bounded step budget**, producing:
- a **final numeric score**, and  
- a **visual overlay** comparing the walked path against the reference route.

---

## Main Idea

The core idea is to test whether an agent can reliably translate **visual observations** (map geometry + Street View perspective) into **sequential control decisions** that follow a target route and reach the goal—rather than solving a single-shot QA problem.

Tasks are:
- **Deterministic per level** (for fair comparison)
- **Grounded in the real world** via **Google Maps APIs**

---

## What the Benchmark Tests (High Level)

ASIN evaluates **spatial intelligence for navigation**, including:

- Aligning an **egocentric view** with a **top-down map**
- Maintaining **orientation**
- Planning **multi-step movements**
- **Recovering from mistakes**
- Knowing **when to stop**

The benchmark emphasizes **consistent progress toward the goal** and **adherence to the intended route**, rather than luck or single-step heuristics.

---

## What the Benchmark Tests (Low Level)

At the low level, the purple agent must repeatedly select **one control command per step**, balancing heading changes and forward motion under a strict budget.

Specifically, the benchmark probes whether the agent can:

1. Infer **heading and local topology** from Street View  
2. Map that inference onto the **2D route geometry**  
3. Execute **correct turns and forward moves** at the right times  
4. **Terminate near the destination**

Performance is evaluated from the **full trajectory** (walked path) relative to the **reference polyline**, not just the final location.

---

## What Is Given to Solve Each Task

At the start of each task, the agent receives:

- A short **instruction prompt**
- A **2D static map** visualizing:
  - the reference route polyline
  - labeled waypoint markers (`A … final`)
- A **Street View image** showing the agent’s current location and facing direction

On subsequent steps:
- The agent receives **updated Street View images** as its pose changes
- The **route map remains fixed** as reference context for that level

---

## Action Space

The agent must respond with **exactly one** of the following commands per step:

- `f` — move forward **15 m**
- `l <deg>` — turn left by `<deg>` degrees
- `r <deg>` — turn right by `<deg>` degrees
- `q` — finish the episode

The episode ends when:
- the agent issues `q`, or  
- the step limit is exceeded

The benchmark then returns:
- a **final score**
- a **map overlay** comparing the taken path to the target route
