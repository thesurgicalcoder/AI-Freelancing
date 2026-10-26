from rest_framework.permissions import BasePermission

from .models import User


class IsClient(BasePermission):
    message = 'Only clients can do this.'

    def has_permission(self, request, view):
        return bool(request.user.is_authenticated and request.user.role == User.Role.CLIENT)


class IsFreelancer(BasePermission):
    message = 'Only freelancers can do this.'

    def has_permission(self, request, view):
        return bool(request.user.is_authenticated and request.user.role == User.Role.FREELANCER)
