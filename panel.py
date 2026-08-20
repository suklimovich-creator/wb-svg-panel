#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точка входа wb-svg-panel.

Сам код живёт в пакете wbpanel/ - здесь только запуск, чтобы systemd-юнит
и все инструкции по установке продолжали ссылаться на panel.py.
"""

from wbpanel.web import main

if __name__ == "__main__":
    main()
