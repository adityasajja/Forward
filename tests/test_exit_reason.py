from forward.appdb.trade_journal_repository import _reason_code


def test_reason_code_is_the_rule_not_the_cut_sentence():
    assert _reason_code("stop_loss: down 10.2% against an average of 1,234.56") == "stop_loss"
    assert _reason_code("take_profit: up 4% against an average of 99") == "take_profit"
    assert _reason_code("time_stop") == "time_stop"
