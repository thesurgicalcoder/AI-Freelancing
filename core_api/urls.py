from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    path('auth/register/', views.RegisterView.as_view()),
    path('auth/login/', views.LoginView.as_view()),
    path('auth/refresh/', TokenRefreshView.as_view()),
    # legacy paths used by the existing templates
    path('register/', views.RegisterView.as_view()),
    path('login/', views.LoginView.as_view()),

    path('projects/', views.ProjectListCreateView.as_view(), name='projects'),
    path('projects/<int:pk>/', views.ProjectDetailView.as_view()),
    path('projects/<int:project_id>/milestones/', views.MilestoneListView.as_view()),
    path('projects/<int:project_id>/apply/', views.ProjectApplyView.as_view()),
    path('projects/<int:project_id>/applications/', views.ProjectApplicationsView.as_view()),
    path('applications/<int:pk>/decide/', views.ApplicationDecideView.as_view()),

    path('wallet/', views.WalletView.as_view()),
    path('wallet/deposit/', views.DepositView.as_view()),
    path('milestones/<int:pk>/fund/', views.FundMilestoneView.as_view()),
    path('milestones/<int:pk>/submit/', views.MilestoneSubmitView.as_view()),
    path('milestones/<int:pk>/submissions/', views.MilestoneSubmissionsView.as_view()),
    path('submissions/<int:pk>/decide/', views.SubmissionDecideView.as_view()),
    path('submissions/mine/', views.MySubmissionsView.as_view()),
]
