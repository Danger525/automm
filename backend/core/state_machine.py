from typing import Dict, Set
from backend.domain.enums import DealStatus


class InvalidStateTransitionError(Exception):
    def __init__(self, current_status: DealStatus, target_status: DealStatus):
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"Invalid deal state transition from '{current_status.value}' to '{target_status.value}'."
        )


VALID_TRANSITIONS: Dict[DealStatus, Set[DealStatus]] = {
    DealStatus.CREATED: {
        DealStatus.WAITING_FOR_AGREEMENT,
        DealStatus.CANCELLED,
    },
    DealStatus.WAITING_FOR_AGREEMENT: {
        DealStatus.AGREED,
        DealStatus.CANCELLED,
    },
    DealStatus.AGREED: {
        DealStatus.ESCROW_CREATING,
        DealStatus.CANCELLED,
    },
    DealStatus.ESCROW_CREATING: {
        DealStatus.WAITING_FOR_PAYMENT,
        DealStatus.CANCELLED,
    },
    DealStatus.WAITING_FOR_PAYMENT: {
        DealStatus.PAYMENT_DETECTED,
        DealStatus.CANCELLED,
    },
    DealStatus.PAYMENT_DETECTED: {
        DealStatus.FUNDED,
        DealStatus.WAITING_FOR_PAYMENT,  # Reverted if tx drops/fails
        DealStatus.CANCELLED,
    },
    DealStatus.FUNDED: {
        DealStatus.DELIVERING,
        DealStatus.DELIVERED,
        DealStatus.RELEASE_PENDING,
        DealStatus.REFUND_PENDING,
        DealStatus.DISPUTED,
    },
    DealStatus.DELIVERING: {
        DealStatus.DELIVERED,
        DealStatus.DISPUTED,
    },
    DealStatus.DELIVERED: {
        DealStatus.RELEASE_PENDING,
        DealStatus.REFUND_PENDING,
        DealStatus.DISPUTED,
    },
    DealStatus.RELEASE_PENDING: {
        DealStatus.RELEASED,
        DealStatus.FUNDED,  # If on-chain broadcast failed, return to funded for retry
        DealStatus.DISPUTED,
    },
    DealStatus.RELEASED: {
        DealStatus.COMPLETED,
    },
    DealStatus.COMPLETED: set(),
    DealStatus.DISPUTED: {
        DealStatus.RELEASE_PENDING,  # Staff or buyer resolves dispute in favor of seller
        DealStatus.REFUND_PENDING,   # Staff resolves dispute in favor of buyer
    },
    DealStatus.REFUND_PENDING: {
        DealStatus.REFUNDED,
        DealStatus.DISPUTED,  # If on-chain refund broadcast failed, return for retry
    },
    DealStatus.REFUNDED: {
        DealStatus.COMPLETED,
        DealStatus.CANCELLED,
    },
    DealStatus.CANCELLED: set(),
}


def can_transition(current: DealStatus, target: DealStatus) -> bool:
    return target in VALID_TRANSITIONS.get(current, set())


def validate_transition(current: DealStatus, target: DealStatus) -> None:
    if not can_transition(current, target):
        raise InvalidStateTransitionError(current, target)
