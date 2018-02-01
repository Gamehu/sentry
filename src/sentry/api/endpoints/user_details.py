from __future__ import absolute_import

import logging

from django.conf import settings
from django.contrib.auth import logout
from rest_framework import serializers, status
from rest_framework.response import Response

from sentry import roles
from sentry.api import client
from sentry.api.bases.user import UserEndpoint
from sentry.api.decorators import sudo_required
from sentry.api.serializers import serialize
from sentry.api.serializers.models.user import DetailedUserSerializer
from sentry.auth.superuser import is_active_superuser
from sentry.models import Organization, OrganizationMember, OrganizationStatus, User, UserOption

delete_logger = logging.getLogger('sentry.deletions.ui')


class BaseUserSerializer(serializers.ModelSerializer):
    def validate_username(self, attrs, source):
        value = attrs[source]
        if User.objects.filter(username__iexact=value).exclude(id=self.object.id).exists():
            raise serializers.ValidationError('That username is already in use.')
        return attrs

    def validate(self, attrs):
        attrs = super(BaseUserSerializer, self).validate(attrs)

        if self.object.email == self.object.username:
            if attrs.get('username', self.object.email) != self.object.email:
                attrs.setdefault('email', attrs['username'])

        return attrs

    def restore_object(self, attrs, instance=None):
        instance = super(BaseUserSerializer, self).restore_object(attrs, instance)
        instance.is_active = attrs.get('isActive', instance.is_active)
        return instance


class UserSerializer(BaseUserSerializer):
    class Meta:
        model = User
        fields = ('name', 'username', 'email')

    def validate_username(self, attrs, source):
        value = attrs[source]
        if User.objects.filter(username__iexact=value).exclude(id=self.object.id).exists():
            raise serializers.ValidationError('That username is already in use.')
        return attrs

    def validate(self, attrs):
        for field in settings.SENTRY_MANAGED_USER_FIELDS:
            attrs.pop(field, None)

        attrs = super(UserSerializer, self).validate(attrs)

        return attrs


class AdminUserSerializer(BaseUserSerializer):
    isActive = serializers.BooleanField(source='is_active')

    class Meta:
        model = User
        # no idea wtf is up with django rest framework, but we need is_active
        # and isActive
        fields = ('name', 'username', 'isActive', 'email')
        # write_only_fields = ('password',)


class UserDetailsEndpoint(UserEndpoint):
    def get(self, request, user):
        data = serialize(user, request.user, DetailedUserSerializer())
        return Response(data)

    def put(self, request, user):
        if is_active_superuser(request):
            serializer_cls = AdminUserSerializer
        else:
            serializer_cls = UserSerializer
        serializer = serializer_cls(user, data=request.DATA, partial=True)

        if serializer.is_valid():
            user = serializer.save()

            options = request.DATA.get('options', {})
            if options.get('seenReleaseBroadcast'):
                UserOption.objects.set_value(
                    user=user,
                    key='seen_release_broadcast',
                    value=options.get('seenReleaseBroadcast'),
                )
            return Response(serialize(user, request.user))

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @sudo_required
    def delete(self, request, user):
        """
        Delete User Account

        Also removes organizations if they are an owner
        :pparam string user_id: user id
        :param list organizations: List of organization ids to remove
        :auth required:
        """

        # from `frontend/remove_account.py`
        org_list = Organization.objects.filter(
            member_set__role=roles.get_top_dog().id,
            member_set__user=user,
            status=OrganizationStatus.VISIBLE,
        )
        org_results = []
        for org in sorted(org_list, key=lambda x: x.name):
            # O(N) query
            org_results.append({
                'organization': org,
                'single_owner': org.has_single_owner(),
            })

        avail_org_slugs = set([o['organization'].slug for o in org_results])
        orgs_to_remove = set(request.DATA.get('organizations')).intersection(avail_org_slugs)

        for result in org_results:
            if result['single_owner']:
                orgs_to_remove.add(result['organization'].slug)

        delete_logger.info(
            'user.deactivate',
            extra={
                'actor_id': request.user.id,
                'ip_address': request.META['REMOTE_ADDR'],
            }
        )

        for org_slug in orgs_to_remove:
            client.delete(
                path='/organizations/{}/'.format(org_slug),
                request=request,
                is_sudo=True)

        remaining_org_ids = [
            o.id for o in org_list if o.slug in avail_org_slugs.difference(orgs_to_remove)
        ]

        if remaining_org_ids:
            OrganizationMember.objects.filter(
                organization__in=remaining_org_ids,
                user=request.user,
            ).delete()

        User.objects.filter(
            id=request.user.id,
        ).update(
            is_active=False,
        )

        logout(request)

        return Response(status=status.HTTP_204_NO_CONTENT)
