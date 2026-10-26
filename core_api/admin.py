from django.contrib import admin

from . import models

for m in (models.User, models.Wallet, models.Project, models.Milestone, models.AcceptanceCriterion,
          models.ProjectApplication, models.Submission, models.CriterionResult):
    admin.site.register(m)


@admin.register(models.LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    """Read-only: the ledger is append-only."""
    list_display = ('created_at', 'kind', 'wallet', 'milestone', 'amount', 'txn_id')

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
