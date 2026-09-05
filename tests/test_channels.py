"""Channels report send outcomes; the lifecycle decides what to do with them."""

from app.channels import (
    Channel,
    ScriptedChannel,
    SendOutcome,
    SimulatedChannel,
)


def test_scripted_channel_defaults_to_sent():
    channel = ScriptedChannel(Channel.EMAIL)
    result = channel.send("a@example.com", "hello")
    assert result.outcome is SendOutcome.SENT
    assert result.reference is not None


def test_scripted_channel_follows_its_script_in_order():
    channel = ScriptedChannel(
        Channel.SMS, [SendOutcome.FAILED, SendOutcome.FAILED, SendOutcome.SENT]
    )
    outcomes = [channel.send("+100", "hi").outcome for _ in range(3)]
    assert outcomes == [SendOutcome.FAILED, SendOutcome.FAILED, SendOutcome.SENT]
    assert channel.sends == 3


def test_scripted_failure_has_no_reference_but_an_error():
    channel = ScriptedChannel(Channel.EMAIL, [SendOutcome.FAILED])
    result = channel.send("a@example.com", "hello")
    assert result.outcome is SendOutcome.FAILED
    assert result.reference is None
    assert result.error


def test_scripted_channel_runs_off_the_end_as_sent():
    channel = ScriptedChannel(Channel.EMAIL, [SendOutcome.FAILED])
    channel.send("a@example.com", "one")
    assert channel.send("a@example.com", "two").outcome is SendOutcome.SENT


def test_simulated_channel_never_fails_at_zero_rate():
    channel = SimulatedChannel(Channel.EMAIL, failure_rate=0.0, seed=1)
    assert all(
        channel.send("a@example.com", "hi").outcome is SendOutcome.SENT
        for _ in range(50)
    )


def test_simulated_channel_always_fails_at_full_rate():
    channel = SimulatedChannel(Channel.SMS, failure_rate=1.0, seed=1)
    assert all(
        channel.send("+100", "hi").outcome is SendOutcome.FAILED for _ in range(50)
    )


def test_simulated_channel_is_deterministic_for_a_seed():
    a = SimulatedChannel(Channel.EMAIL, failure_rate=0.5, seed=7)
    b = SimulatedChannel(Channel.EMAIL, failure_rate=0.5, seed=7)
    a_out = [a.send("a@example.com", "hi").outcome for _ in range(20)]
    b_out = [b.send("a@example.com", "hi").outcome for _ in range(20)]
    assert a_out == b_out


def test_reference_is_prefixed_with_the_channel():
    result = ScriptedChannel(Channel.SMS).send("+100", "hi")
    assert result.reference.startswith("sms-")
