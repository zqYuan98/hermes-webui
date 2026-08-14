/* Hermes Hub — Agent monthly cost calculations.
 * Pure helpers only: no DOM, network, or storage access.  Keeping the finance
 * rules here makes totals independently testable and reusable by the Hub UI.
 */
(function () {
  'use strict';
  if (window.AgentCost) return;

  function pad(value) { return String(value).padStart(2, '0'); }

  function currentMonth() {
    var now = new Date();
    return now.getFullYear() + '-' + pad(now.getMonth() + 1);
  }

  function normalizeMonth(value, fallback) {
    var match = String(value || '').trim().match(/^(\d{4})-(0[1-9]|1[0-2])/);
    return match ? match[1] + '-' + match[2] : (arguments.length > 1 ? fallback : currentMonth());
  }

  function shiftMonth(value, delta) {
    var month = normalizeMonth(value);
    var parts = month.split('-');
    var date = new Date(Number(parts[0]), Number(parts[1]) - 1 + Number(delta || 0), 1);
    return date.getFullYear() + '-' + pad(date.getMonth() + 1);
  }

  function previousMonth(value) { return shiftMonth(value, -1); }

  function hasAmount(value) {
    if (value === null || typeof value === 'undefined') return false;
    if (typeof value === 'string' && value.trim() === '') return false;
    return isFinite(Number(value));
  }

  function amount(value) { return hasAmount(value) ? Number(value) : 0; }

  function addClassified(result, row, value, recurring) {
    var category = row.costCategory || (recurring ? 'agent_subscription' :
      (row.costType === 'usage' ? 'model_usage' : 'other'));
    if (category === 'cloud_server') {
      result.cloudServer += value;
      result.infrastructure += value;
    } else if (category === 'proxy_subscription') {
      result.proxyNetwork += value;
      result.infrastructure += value;
    } else if (category === 'model_usage') {
      result.usage += value;
    } else if (category === 'agent_subscription') {
      result.subscription += value;
    } else if (!row.costCategory && !recurring && row.costType === 'infrastructure') {
      // Legacy rows did not distinguish cloud/proxy. Keep the infrastructure
      // roll-up while surfacing them under "other" so visible structure sums
      // exactly to the total instead of silently losing the amount.
      result.infrastructure += value;
      result.other += value;
    } else {
      result.other += value;
    }
  }

  function validateSubscription(row) {
    row = row || {};
    var start = normalizeMonth(row.startMonth, '');
    var end = normalizeMonth(row.endMonth, '');
    if (!start) return '固定订阅必须填写开始月份';
    if (row.status === 'paused' && !end) return '暂停订阅必须填写结束月份';
    if (end && end < start) return '结束月份不能早于开始月份';
    return '';
  }

  function billingRange(row) {
    if (row && row.month) {
      var expenseMonth = normalizeMonth(row.month, '');
      return expenseMonth ? { start: expenseMonth, end: expenseMonth } : null;
    }
    var start = row && normalizeMonth(row.startMonth, '');
    if (!start) return null;
    return { start: start, end: normalizeMonth(row.endMonth, '') || '9999-12' };
  }

  function validateCloudIdentity(row, data, editingId) {
    row = row || {};
    if (row.costCategory !== 'cloud_server' || row.amountStatus !== 'confirmed') return '';
    var resourceId = String(row.billingResourceId || '').trim().toLowerCase();
    if (!resourceId) return '已确认云服务器费用必须填写云资源/实例标识';
    var rowRange = billingRange(row);
    var duplicate = ((data && data.subscriptions) || []).concat((data && data.expenses) || []).some(function (existing) {
      if (!existing || existing.id === editingId || existing.costCategory !== 'cloud_server') return false;
      if (existing.amountStatus !== 'confirmed') return false;
      if (String(existing.billingResourceId || '').trim().toLowerCase() !== resourceId) return false;
      var existingRange = billingRange(existing);
      if (!rowRange || !existingRange) return true;
      return rowRange.start <= existingRange.end && existingRange.start <= rowRange.end;
    });
    return duplicate ? '云资源/实例标识在相同计费月份重复，请先核对并合并记录' : '';
  }

  function monthInRange(row, month) {
    var start = row.startMonth ? normalizeMonth(row.startMonth, '') : '';
    var end = row.endMonth ? normalizeMonth(row.endMonth, '') : '';
    // Missing start month is invalid data, never an implicit "since forever".
    if (!start) return false;
    if (month < start) return false;
    if (end && month > end) return false;
    return true;
  }

  function subscriptionApplies(row, month) {
    if (!row || !monthInRange(row, month)) return false;
    if (row.status === 'configured') return false;
    if (row.status === 'active') return true;
    // A paused row remains authoritative for its explicitly closed historical
    // range.  Without endMonth there is no safe basis for charging it.
    return row.status === 'paused' && Boolean(row.endMonth);
  }

  function subscriptionNeedsAmount(row, month) {
    if (!row) return false;
    // Invalid lifecycle dates are actionable data-quality problems. Keep them
    // pending in whichever month is being inspected instead of charging history
    // or silently dropping the record.
    if (validateSubscription(row)) return true;
    if (!monthInRange(row, month)) return false;
    if (row.status === 'configured') return true;
    return subscriptionApplies(row, month) &&
      (row.amountStatus !== 'confirmed' || !hasAmount(row.monthlyAmount));
  }

  function monthlyRecordCount(data, monthValue) {
    var month = normalizeMonth(monthValue);
    data = data || {};
    var subscriptions = (data.subscriptions || []).filter(function (row) {
      if (!row || validateSubscription(row) || !monthInRange(row, month)) return false;
      return row.status === 'active' || row.status === 'configured' ||
        (row.status === 'paused' && Boolean(row.endMonth));
    }).length;
    var expenses = (data.expenses || []).filter(function (row) {
      return normalizeMonth(row && row.month, '') === month;
    }).length;
    return subscriptions + expenses;
  }

  function totalsForMonth(data, monthValue) {
    var month = normalizeMonth(monthValue);
    var result = {
      month: month,
      subscription: 0,
      usage: 0,
      cloudServer: 0,
      proxyNetwork: 0,
      infrastructure: 0,
      other: 0,
      pendingCount: 0,
      confirmedCount: 0
    };
    data = data || {};

    (data.subscriptions || []).forEach(function (row) {
      if (subscriptionNeedsAmount(row, month)) {
        result.pendingCount += 1;
        return;
      }
      if (!subscriptionApplies(row, month)) return;
      if (row.amountStatus === 'confirmed' && hasAmount(row.monthlyAmount)) {
        addClassified(result, row, amount(row.monthlyAmount), true);
        result.confirmedCount += 1;
      }
    });

    (data.expenses || []).forEach(function (row) {
      if (normalizeMonth(row.month, '') !== month) return;
      if (row.amountStatus === 'no_cost') return;
      if (row.amountStatus !== 'confirmed' || !hasAmount(row.amount)) {
        result.pendingCount += 1;
        return;
      }
      addClassified(result, row, amount(row.amount), false);
      result.confirmedCount += 1;
    });

    // `infrastructure` is a roll-up (cloud + proxy + legacy infra), not an
    // independent bucket. Total only sums mutually exclusive visible buckets.
    result.consumption = result.usage + result.cloudServer + result.proxyNetwork + result.other;
    result.total = result.subscription + result.consumption;
    return result;
  }

  function budgetForMonth(data, month) {
    var rows = (data && data.budgets) || [];
    for (var i = rows.length - 1; i >= 0; i -= 1) {
      if (normalizeMonth(rows[i].month, '') === month && hasAmount(rows[i].amount)) {
        return amount(rows[i].amount);
      }
    }
    return null;
  }

  function summarize(data, monthValue) {
    var month = normalizeMonth(monthValue);
    var current = totalsForMonth(data, month);
    var previous = totalsForMonth(data, previousMonth(month));
    var budget = budgetForMonth(data, month);
    current.budget = budget;
    current.budgetVariance = budget === null ? null : current.total - budget;
    current.previousTotal = previous.total;
    current.momAmount = current.total - previous.total;
    current.momPercent = previous.total === 0 ? null : (current.momAmount / previous.total) * 100;
    return current;
  }

  function trend(data, monthValue, count) {
    var month = normalizeMonth(monthValue);
    var size = Math.max(1, Number(count) || 6);
    var rows = [];
    for (var offset = size - 1; offset >= 0; offset -= 1) {
      rows.push(totalsForMonth(data, shiftMonth(month, -offset)));
    }
    return rows;
  }

  function allocationBreakdown(data, monthValue) {
    var month = normalizeMonth(monthValue);
    var result = {
      company_self: 0,
      customer_project: 0,
      internal_department: 0,
      personal_reimbursement: 0,
      unassigned: 0
    };
    function add(row, value) {
      // Backward compatibility: records created before allocation existed were
      // company-operated Agent costs, matching the UI's default selection.
      var requested = row.allocation || 'company_self';
      var key = Object.prototype.hasOwnProperty.call(result, requested)
        ? requested : 'unassigned';
      result[key] += value;
    }
    ((data && data.subscriptions) || []).forEach(function (row) {
      if (!subscriptionApplies(row, month) || row.amountStatus !== 'confirmed' || !hasAmount(row.monthlyAmount)) return;
      add(row, amount(row.monthlyAmount));
    });
    ((data && data.expenses) || []).forEach(function (row) {
      if (normalizeMonth(row.month, '') !== month || row.amountStatus !== 'confirmed' || !hasAmount(row.amount)) return;
      add(row, amount(row.amount));
    });
    return result;
  }

  function renewals(data, fromValue, days) {
    var from = fromValue ? new Date(fromValue) : new Date();
    if (isNaN(from.getTime())) from = new Date();
    var until = new Date(from.getTime() + (Number(days) || 45) * 86400000);
    return ((data && data.subscriptions) || []).filter(function (row) {
      if (row.status !== 'active' || !row.renewalDate) return false;
      var renewal = new Date(row.renewalDate + 'T00:00:00');
      return !isNaN(renewal.getTime()) && renewal >= from && renewal <= until;
    }).sort(function (a, b) { return String(a.renewalDate).localeCompare(String(b.renewalDate)); });
  }

  window.AgentCost = {
    normalizeMonth: normalizeMonth,
    shiftMonth: shiftMonth,
    previousMonth: previousMonth,
    validateSubscription: validateSubscription,
    subscriptionNeedsAttention: subscriptionNeedsAmount,
    validateCloudIdentity: validateCloudIdentity,
    monthlyRecordCount: monthlyRecordCount,
    summarize: summarize,
    trend: trend,
    allocationBreakdown: allocationBreakdown,
    renewals: renewals,
    hasAmount: hasAmount
  };
})();
