```python
def hx_create_lead(request: OrgHttpRequest) -> HttpResponse:
"""
Handles the creation of new lead, along with associated mobile numbers,
        and email addresses using formsets. Displays the form and saves data if
        valid.
        """

        if request.method == "POST":
# Binding the submitted data to the form.
lead_form = LeadCreateForm(request.POST)
    mobile_formset = MobileNumberFormSet(request.POST, prefix="mobile")
    email_formset = EmailFormSet(request.POST, prefix="email")
address_form = LeadAddressForm(request.POST)

    if (
            lead_form.is_valid()
            and mobile_formset.is_valid()
            and email_formset.is_valid()
            and address_form.is_valid()
       ):
        lead = lead_form.save()

# Assigning the lead to the formset instances.
        mobile_formset.instance = lead
        email_formset.instance = lead
        address_form.instance = lead

    mobile_formset.save()
    email_formset.save()
address_form.save()

    res = render(request, "leads/forms/lead_create.html")
    res = trigger_client_event(
            res,
            "message",
            {"level": "success", "message": "Lead Created Successfully!"},
            )
    res = trigger_client_event(
            res,
            "lead-created",
            )
    return res
    else:
# The invalid response with error messages is returned.
    context = {
        "lead_form": lead_form,
        "mobile_formset": mobile_formset,
        "email_formset": email_formset,
        "address_form": address_form,
    }

res = render(request, "leads/forms/lead_create.html", context)
res = trigger_client_event(
        res,
        "message",
        {"level": "error", "message": "Error adding mobile number."},
        )

return res

# The get request is performed.
lead_form = LeadCreateForm()
    mobile_formset = MobileNumberFormSet(prefix="mobile")
    email_formset = EmailFormSet(prefix="email")
address_form = LeadAddressForm()

    context = {
        "lead_form": lead_form,
        "mobile_formset": mobile_formset,
        "email_formset": email_formset,
        "address_form": address_form,
    }
return render(request, "leads/forms/lead_create.html", context)
```

```django
{% load static %}
<c-form-wrapper action="{{ request.path }}" method="post" class="modal-content">
  <div class="modal-header">
    <h5 class="modal-title">Create Lead</h5>
    <button type="button" class="close" data-dismiss="modal" aria-label="Close">
      <span aria-hidden="true">X</span>
    </button>
  </div>
  <div class="modal-body">
    <c-form-errors :form="lead_form" />

    <c-form-hidden :field="lead_form.organization" />

    <div class="row">
      <div class="col-md-4 col-sm-12">
        <c-form-input :field="lead_form.first_name" />
      </div>
      <div class="col-md-4 col-sm-12">
        <c-form-input :field="lead_form.middle_name" />
      </div>
      <div class="col-md-4 col-sm-12">
        <c-form-input :field="lead_form.last_name" />
      </div>
      <div class="col-md-4 col-sm-12">
        <c-form-input :field="lead_form.source" />
      </div>
    </div>
    <div class="row">
      <div class="col-md-6 col-sm-12">
        <div class="card mb-3">
          <div class="card-header">Mobile Numbers</div>
          <div id="mobile-formset-container" class="card-body">
            {{ mobile_formset.management_form }}
            {% for form in mobile_formset %}
              <div class="form-group">
                <c-form-input :field="form.mobile_number" />
              </div>
            {% endfor %}
          </div>
          <div class="card-footer">
            <button type="button"
                    class="btn btn-outline-primary btn-sm"
                    hx-get="{% url 'hx-add-mobile-formset-input' slug=request.organization.slug %}"
                    hx-target="#mobile-formset-container"
                    hx-include="input[name='mobile-TOTAL_FORMS']"
                    hx-swap="beforeend">Add Mobile</button>
          </div>
        </div>
      </div>
      <div class="col-md-6 col-sm-12">
        <div class="card mb-3">
          <div class="card-header">Email Addresses</div>
          <div id="email-formset-container" class="card-body">
            {{ email_formset.management_form }}
            {% for form in email_formset %}
              <div class="form-group">
                <c-form-input :field="form.email" />
              </div>
            {% endfor %}
          </div>
          <div class="card-footer">
            <button type="button"
                    class="btn btn-outline-primary btn-sm"
                    hx-get="{% url 'hx-add-email-formset-input' slug=request.organization.slug %}"
                    hx-target="#email-formset-container"
                    hx-include="input[name='email-TOTAL_FORMS']"
                    hx-swap="beforeend">Add Email</button>
          </div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-header">Address</div>
      <div class="card-body">
        <div class="row">
          <div class="col-md-6 col-sm-12">
            <c-form-input :field="address_form.flat_building" />
          </div>
          <div class="col-md-6 col-sm-12">
            <c-form-input :field="address_form.street" />
          </div>
        </div>
        <div class="row">
          <div class="col-md-6 col-sm-12">
            <c-form-input :field="address_form.area" />
          </div>
          <div class="col-md-6 col-sm-12">
            <c-form-input :field="address_form.landmark" />
          </div>
        </div>
        <div class="row">
          <div class="col-md-4 col-sm-12">
            <c-form-input :field="address_form.city" />
          </div>
          <div class="col-md-4 col-sm-12">
            <c-form-input :field="address_form.state" />
          </div>
          <div class="col-md-4 col-sm-12">
            <c-form-input :field="address_form.pincode" />
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="modal-footer">
    <c-submit-button label="Submit" />
    <button type="button" class="btn btn-outline-danger" data-dismiss="modal">Cancel</button>
  </div>
</c-form-wrapper>
```

```django
{% extends "base.html" %}
{% load static %}
<!-- -->
{% block content %}
  <h4>
    Welcome, to <span class="text-primary">{{ request.organization.name }}</span>
  </h4>
  <br />
  <div class="my-3">
    <button type="button"
            hx-get="{% url 'hx-create-lead' slug=request.organization.slug %}"
            hx-target="#modal-form"
            class="btn btn-primary">
      <i data-feather="plus" class="icon-sm"></i> Add Lead
    </button>
    <a href="{% url 'sale-create' slug=request.organization.slug %}"
       class="btn btn-warning">+ Add New Sale</a>
  </div>
  <!-- Chart Data (Hidden) -->
  <!-- <div id="chartData" style="display: none;">{{ chart_data|json_script:""|safe }}</div> -->
  <input type="hidden" name="org_slug" value="{{ request.organization.slug }}" />
  <!-- <div class="row">
    <div class="col-md-4 mb-4">
      <div class="metric-card">
        <div class="d-flex justify-content-between">
          <div>
            <p class="text-muted">Total Leads</p>
            <div class="metric-value">{{ lead_count }}</div>
            <div class="metric-change {% if recent_lead_count > 0 %}up{% else %}down{% endif %}">
              {{ recent_lead_count }} this month
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="col-md-4 mb-4">
      <div class="metric-card">
        <div class="d-flex justify-content-between">
          <div>
            <p class="text-muted">Memberships Sold</p>
            <div class="metric-value">{{ recent_sales.count }}</div>
            <div class="metric-change {% if chart_data.sales_trend|last > chart_data.sales_trend|first|default:0 %}up{% else %}down{% endif %}">
              {% with last_sale=chart_data.sales_trend|last previous_sale=chart_data.sales_trend|slice:"-2"|first|default:0 %}
                {% if last_sale > previous_sale %}+{% endif %}
                {{ last_sale|default:0 }} this week
              {% endwith %}
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="col-md-4 mb-4">
      <div class="metric-card">
        <div class="d-flex justify-content-between">
          <div>
            <p class="text-muted">Followups</p>
            <div class="metric-value">0</div>
            <div class="metric-change">Coming soon</div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <hr /> -->
  <div class="row">
    <div class="col-md-6">
      <div class="card">
        <div class="card-body">
          <div class="card-title">
            <h6 class="text-danger mb-0">Recent Leads</h6>
          </div>
          <div class="table-responsive-sm">
            <div class="table-container">
              <div id="all-leads-table"
                   hx-trigger="lead-created from:body, lead-updated from:body, lead-deleted from:body, load"
                   hx-get="{% url 'hx-leads-table' slug=request.organization.slug %}"
                   hx-swap="innerHTML"
                   hx-target="#all-leads-table">
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="col-md-6">
      <div class="card border-danger">
        <div class="card-body">
          <div class="card-title d-flex justify-content-between align-items-center">
            <h6 class="text-danger mb-0">Upcoming Membership Expirations.</h6>
          </div>
          <div class="table-responsive-sm">
<div class="table-container"
                id="membership-expirations-table"
                hx-get="{% url 'hx-membership-expirations' slug=request.organization.slug %}"
                hx-trigger="load, lead-deleted from:body"
                hx-swap="outerHTML">
              <div class="text-center py-3">
                <span class="spinner-border spinner-border-sm"></span> Loading...
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="row mt-4">
    <div class="col-md-6">
      <div class="card">
        <div class="card-body">
          <div class="card-title">
            <h6 class="text-danger mb-0">Outstanding Payment Amounts</h6>
          </div>
          <div class="table-wrapper" style="overflow-x: auto; max-width: 100%;">
            <div class="table-container"
                 id="outstanding-payments-table"
                 hx-get="{% url 'hx-outstanding-payments' slug=request.organization.slug %}"
                 hx-trigger="load, lead-deleted from:body"
                 hx-swap="outerHTML">
              <div class="text-center py-3">
                <span class="spinner-border spinner-border-sm"></span> Loading...
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="col-md-6">
      <div class="card">
        <div class="card-body">
          <div class="card-title">
            <h6 class="text-danger mb-0">Recent Membership Sales</h6>
          </div>
          <div class="table-wrapper" style="overflow-x: auto; max-width: 100%;">
            <div class="table-container"
               id="recent-membership-sales-table"
               hx-get="{% url 'hx-recent-membership-sales' slug=request.organization.slug %}"
               hx-trigger="load, lead-deleted from:body"
               hx-swap="outerHTML">
              <div class="text-center py-3">
                <span class="spinner-border spinner-border-sm"></span> Loading...
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
{% endblock content %}
<!-- -->
{% block custom_js %}
  <!-- <script src="{% static 'js/appjs/dashboard.js' %}"></script> -->
{% endblock custom_js %}
```
