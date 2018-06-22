# FAQ

## Why start this project?

We research reinforcement learning and other machine learning algorithms that
are compute-intensive. We also have a network of powerful PCs, but
previously there was no efficient, convenient, and reliable way to
distribute our algorithms across the entire cluster without a lot of manual
work.

Since we use Python extensively, we did research into existing parallel
computing frameworks for Python. The one we liked best was Celery, so we
decided to build a framework on top of Celery that allows tasks to be defined
dynamically.

Another priority for us was to allow CPU resources to be adjusted at any time
without interrupting existing tasks.
