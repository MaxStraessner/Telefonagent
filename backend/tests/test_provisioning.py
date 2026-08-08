import pytest

from app.services.provisioning import ProvisioningConflictError, ProvisioningService


def test_tenant_provisioning_is_idempotent_and_does_not_replace_password(db):
    service = ProvisioningService(db)
    tenant, user = service.provision_tenant(
        slug="provisioning-test",
        name="Provisioning Test",
        industry="services",
        timezone_name="Europe/Berlin",
        username="provisioning-owner",
        display_name="Provisioning Owner",
        email="provisioning@example.test",
        password="first strong provisioning password",
    )
    original_hash = user.password_hash
    same_tenant, same_user = service.provision_tenant(
        slug="provisioning-test",
        name="Provisioning Test",
        industry="services",
        timezone_name="Europe/Berlin",
        username="provisioning-owner",
        display_name="Provisioning Owner",
        email="provisioning@example.test",
        password="a different password is ignored",
    )
    assert same_tenant.id == tenant.id
    assert same_user.id == user.id
    assert same_user.password_hash == original_hash


def test_tenant_provisioning_rejects_conflicting_existing_data(db):
    service = ProvisioningService(db)
    with pytest.raises(ProvisioningConflictError):
        service.provision_tenant(
            slug="provisioning-test",
            name="Different Name",
            industry="services",
            timezone_name="Europe/Berlin",
            username="provisioning-owner",
            display_name="Provisioning Owner",
            email="provisioning@example.test",
            password="first strong provisioning password",
        )
