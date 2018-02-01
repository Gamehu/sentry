from __future__ import absolute_import

import six

from django.core.urlresolvers import reverse

from sentry.models import Organization, OrganizationStatus, User, UserOption
from sentry.testutils import APITestCase


class UserDetailsTest(APITestCase):
    # TODO(dcramer): theres currently no way to look up other users
    # def test_simple(self):
    #     user = self.create_user(email='a@example.com')
    #     user2 = self.create_user(email='b@example.com')

    #     self.login_as(user=user)

    #     url = reverse('sentry-api-0-user-details', kwargs={
    #         'user_id': user2.id,
    #     })
    #     resp = self.client.get(url, format='json')

    #     assert resp.status_code == 200, resp.content
    #     assert resp.data['id'] == six.text_type(user.id)
    #     assert 'identities' not in resp.data

    def test_lookup_self(self):
        user = self.create_user(email='a@example.com')

        self.login_as(user=user)

        url = reverse(
            'sentry-api-0-user-details', kwargs={
                'user_id': 'me',
            }
        )
        resp = self.client.get(url, format='json')

        assert resp.status_code == 200, resp.content
        assert resp.data['id'] == six.text_type(user.id)

    def test_superuser(self):
        user = self.create_user(email='a@example.com')
        superuser = self.create_user(email='b@example.com', is_superuser=True)

        self.login_as(user=superuser, superuser=True)

        url = reverse(
            'sentry-api-0-user-details', kwargs={
                'user_id': user.id,
            }
        )

        resp = self.client.get(url)
        assert resp.status_code == 200, resp.content
        assert resp.data['id'] == six.text_type(user.id)
        assert 'identities' in resp.data
        assert len(resp.data['identities']) == 0


class UserUpdateTest(APITestCase):
    def test_simple(self):
        user = self.create_user(email='a@example.com')

        self.login_as(user=user)

        url = reverse(
            'sentry-api-0-user-details', kwargs={
                'user_id': 'me',
            }
        )

        resp = self.client.put(
            url,
            data={
                'name': 'hello world',
                'username': 'b@example.com',
                'options': {
                    'seenReleaseBroadcast': True
                }
            }
        )
        assert resp.status_code == 200, resp.content
        assert resp.data['id'] == six.text_type(user.id)

        user = User.objects.get(id=user.id)
        assert user.name == 'hello world'
        assert user.email == 'b@example.com'
        assert user.username == user.email
        assert UserOption.objects.get_value(
            user=user,
            key='seen_release_broadcast',
        ) is True

    def test_superuser(self):
        user = self.create_user(email='a@example.com')
        superuser = self.create_user(email='b@example.com', is_superuser=True)

        self.login_as(user=superuser, superuser=True)

        url = reverse(
            'sentry-api-0-user-details', kwargs={
                'user_id': user.id,
            }
        )

        resp = self.client.put(
            url,
            data={
                'name': 'hello world',
                'email': 'c@example.com',
                'username': 'foo',
                'isActive': 'false',
            }
        )
        assert resp.status_code == 200, resp.content
        assert resp.data['id'] == six.text_type(user.id)

        user = User.objects.get(id=user.id)
        assert user.name == 'hello world'
        assert user.email == 'c@example.com'
        assert user.username == 'foo'
        assert not user.is_active

    def test_close_account(self):
        self.login_as(user=self.user)
        org_single_owner = self.create_organization(name="A", owner=self.user)
        user2 = self.create_user(email="user2@example.com")
        org_with_other_owner = self.create_organization(name="B", owner=self.user)
        org_as_other_owner = self.create_organization(name="C", owner=user2)
        not_owned_org = self.create_organization(name="D", owner=user2)

        self.create_member(
            user=user2,
            organization=org_with_other_owner,
            role='owner',
        )

        self.create_member(
            user=self.user,
            organization=org_as_other_owner,
            role='owner',
        )

        url = reverse(
            'sentry-api-0-user-details', kwargs={
                'user_id': self.user.id,
            }
        )

        response = self.client.delete(url, data={
            'organizations': [org_with_other_owner.slug, org_as_other_owner.slug, not_owned_org.slug]
        })

        # deletes org_single_owner even though it wasn't specified in array
        # because it has a single owner
        assert Organization.objects.get(
            id=org_single_owner.id).status == OrganizationStatus.PENDING_DELETION
        # should delete org_with_other_owner, and org_as_other_owner
        assert Organization.objects.get(
            id=org_with_other_owner.id).status == OrganizationStatus.PENDING_DELETION
        assert Organization.objects.get(
            id=org_as_other_owner.id).status == OrganizationStatus.PENDING_DELETION
        # should NOT delete `not_owned_org`
        assert Organization.objects.get(id=not_owned_org.id).status == OrganizationStatus.ACTIVE

        assert response.status_code == 204
