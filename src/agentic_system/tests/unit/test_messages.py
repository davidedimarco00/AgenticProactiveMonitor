from agentic_system.agents.messages import Performative


def test_performative_values_are_stable() -> None:
    assert Performative.REQUEST.value == "REQUEST"
    assert Performative.AGREE.value == "AGREE"
