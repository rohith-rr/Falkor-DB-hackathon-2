"""Wayfinder-Bench scoring and the BM25 baseline behave as documented."""
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('bench_run',Path(__file__).resolve().parents[1]/'benchmarks/wayfinder_bench/run.py')
bench=importlib.util.module_from_spec(spec); spec.loader.exec_module(bench)


def test_scoring_answerable_and_unanswerable():
    assert bench.score(['a','b'],['a','b'],True)=={'correct':True,'precision':1.0,'recall':1.0,'f1':1.0}
    partial=bench.score(['a','x'],['a','b'],True)
    assert partial['correct'] is False and partial['precision']==0.5 and partial['recall']==0.5 and partial['f1']==0.5
    assert bench.score([],['a'],True)['f1']==0.0
    assert bench.score([],[],False)['correct'] is True and bench.score(['x'],[],False)['correct'] is False


def test_summary_counts_unanswerable_separately():
    rows=[{'answerable':True,'type':'code','correct':True,'f1':1.0,'recall':1.0},
          {'answerable':False,'type':'code','correct':False,'f1':None,'recall':None}]
    s=bench.summarize(rows)
    assert s['answerable_f1']==1.0 and s['unanswerable_correct']==0.0 and s['overall_correct']==0.5


def test_bm25_ranks_matching_text_and_returns_nothing_without_overlap():
    docs=[{'id':'a','text':'Import Slack messages and threads'},{'id':'b','text':'Dashboard connection errors'}]
    ranker=bench.BM25(docs)
    assert ranker.top('Which Slack threads were imported?')[0]=='a'
    assert ranker.top('zzz qqq')==[]
