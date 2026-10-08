# -*- coding: utf-8 -*-
"""大脑输出摘要：静默成功（exit 0 无 stdout）要给出思考型模型诊断而非
含糊的"(无输出)"。"""
from xy_gate.brain import BrainResult, summarize_result


def test_summary_normal_stdout():
    r = BrainResult(True, 0, "第一行\n第二行", "", 1.0)
    assert summarize_result(r) == "第一行\n第二行"


def test_summary_silent_success_hint():
    r = BrainResult(True, 0, "", "", 1.0)
    s = summarize_result(r)
    assert "思考型" in s and "content" in s


def test_summary_failure_shows_stderr():
    r = BrainResult(False, 1, "", "dsh: TRANSPORT: boom", 1.0)
    assert "TRANSPORT" in summarize_result(r)
