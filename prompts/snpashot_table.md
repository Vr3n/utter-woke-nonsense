# Table pagination docs.

We have adapted our tables to have paginations successfully.\
Now we need to standardize it to all the modules.

Read these docs:

- https://freedium-mirror.cfd/https://medium.com/@ezequiel.grondona/row-actions-with-django-tables2-and-materializecss-3e4618544db0
- https://taskbadger.net/blog/tables.html
- https://django-tables2.readthedocs.io/en/latest/pages/table-data.html

Below is the Guide for adoption.

# 1. Table Object for the Model.

- Take the `SingleTableView` directly if there aren't custom logic beside model fields.
- use `tables.Table` for custom logic or columns.

```python
import django_tables2 as tables
from .models import LeadMaster


class LeadTable(tables.Table):
    full_name = tables.Column(accessor="full_name", verbose_name="Name")
    mobile_number = tables.Column(
        accessor="first_mobile__0__mobile_number",
        verbose_name="Mobile Number",
        default="-",
    )
    email = tables.Column(
        accessor="first_email__0__email", verbose_name="Email", default="-"
    )
    detail = tables.TemplateColumn(
        template_code='<a href="{{ record.get_absolute_url }}" class="btn btn-sm btn-info">Detail</a>',
        verbose_name="",
        orderable=False,
    )
    delete = tables.TemplateColumn(
        template_code="""
            <button class="btn btn-sm btn-danger"
                    hx-delete="{% url 'hx-delete-lead' slug=request.organization.slug pk=record.pk %}"
                    hx-confirm="Delete this lead?"
                    hx-target="closest tr"
                    hx-swap="outerHTML swap:300ms">
                Delete
            </button>
        """,
        verbose_name="",
        orderable=False,
    )

    class Meta:
        model = LeadMaster
        template_name = "tables/hx-bootstrap4.html"
        fields = ("full_name", "mobile_number", "email", "detail", "delete")
        attrs = {"class": "table table-hover"}
```

# 2. Write the Table view.

For leads we wrote the following view to get the table.

- The template will be same for all modules as it's universal.

```python
@login_required
@organization_slug_required
def hx_leads_table(request: OrgHttpRequest) -> HttpResponse:
    """
    HTMX endpoint: returns paginated leads table.
    """

    VALID_PER_PAGE = {5, 10, 25, 50, 100}

    # Validate per_page — never trust GET params directly
    per_page = int(request.GET.get("per_page", 5))
    if per_page not in VALID_PER_PAGE:
        per_page = 5

    leads = (
        LeadMaster.objects.filter(organization=request.organization)
        .prefetch_related(
            Prefetch(
                "mobile_numbers",
                queryset=LeadMobileNumberMaster.objects.order_by("created_at"),
                to_attr="first_mobile",
            ),
            Prefetch(
                "emails",
                queryset=LeadEmailAddressMaster.objects.order_by("created_at"),
                to_attr="first_email",
            ),
        )
        .order_by("-created_at")
    )

    table = LeadTable(leads, request=request)
    # RequestConfig reads ?page= from GET automatically
    RequestConfig(request, paginate={"per_page": per_page}).configure(table)

    context = {
        "table": table,
        "htmx_url": request.path,
        "htmx_target": "#all-leads-table",
        "per_page_options": [5, 10, 25, 50, 100],
        "per_page": per_page,
    }

    return render(request, "tables/hx-bootstrap4.html", context)
```

- Don't forget to create url.

```python
path("hx/all-leads/table/", views.hx_leads_table, name="hx-leads-table")
```

## Page container and Table loading.

- Add this to wherever you want the table to load.
- Trigger the table view on load.

```jinja
<div class="table-responsive"
    id="all-leads-table"
    hx-trigger="lead-created from:body, lead-updated from:body, load"
    hx-get="{% url 'hx-leads-table' slug=request.organization.slug %}"
    hx-swap="innerHTML"
    hx-target="#all-leads-table">
</div>
```
