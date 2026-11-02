# DogBuddyCn — task kernel (shadow-first)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Task kernel: durable triggers/queue/outbox, introduced shadow-first.

The old admin/window path remains the source of truth in 0.19.x. This package
records intake and durable delivery so the next stage can switch dispatch over
without changing user-visible behaviour first.
"""
from .store import KernelStore

__all__ = ["KernelStore"]
