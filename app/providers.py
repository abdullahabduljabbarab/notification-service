"""The channel providers the live service delivers through.

Built once at process start so each provider's idempotency store (its record of
which keys have already succeeded) persists across requests within a process,
which is what protects the crash window between a send and its commit. Tests
inject scripted providers instead of these.
"""

from app import config
from app.channels import Channel, ChannelProvider, SimulatedChannel


def default_channels() -> dict[Channel, ChannelProvider]:
    return {
        Channel.EMAIL: SimulatedChannel(Channel.EMAIL, config.EMAIL_FAILURE_RATE),
        Channel.SMS: SimulatedChannel(Channel.SMS, config.SMS_FAILURE_RATE),
    }
