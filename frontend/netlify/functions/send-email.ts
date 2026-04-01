// netlify/functions/send-email.ts
// ComplyWithJudy — Email delivery via Gmail SMTP
// Routes: POST /.netlify/functions/send-email/scan-complete

import type { Handler } from '@netlify/functions';
import * as nodemailer from 'nodemailer';

const GMAIL_USER = process.env.GMAIL_USER || 'judy@vip.thecolyerteam.com';
const GMAIL_APP_PASSWORD = process.env.GMAIL_APP_PASSWORD || '';

const transporter = nodemailer.createTransport({
  service: 'gmail',
  auth: {
    user: GMAIL_USER,
    pass: GMAIL_APP_PASSWORD,
  },
});

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Content-Type': 'application/json',
};

// ----------------------------------------------------------------
// Email templates
// ----------------------------------------------------------------

interface ScanEmailData {
  to: string;
  scanId: string;
  url: string;
  score: number;
  profession: string;
  passed: number;
  warnings: number;
  failed: number;
  totalChecks: number;
  isPaid: boolean;
  checks?: {
    name: string;
    status: string;
    description: string;
    fix?: string;
  }[];
}

function scoreColor(score: number): string {
  if (score >= 80) return '#16a34a';
  if (score >= 60) return '#d97706';
  return '#dc2626';
}

function scoreLabel(score: number): string {
  if (score >= 80) return 'Good Standing';
  if (score >= 60) return 'Needs Attention';
  return 'Action Required';
}

function statusIcon(status: string): string {
  switch (status) {
    case 'pass': return '✅';
    case 'warn': return '⚠️';
    case 'fail': return '❌';
    default: return '➖';
  }
}

function professionLabel(profession: string): string {
  return profession === 'lending' ? 'Mortgage / Lending' : 'Real Estate';
}

function buildFreeEmail(data: ScanEmailData): { subject: string; html: string } {
  const color = scoreColor(data.score);
  const label = scoreLabel(data.score);
  const resultsUrl = `https://complywithjudy.com/results/${data.scanId}`;

  const checkRows = (data.checks || [])
    .filter(c => c.status !== 'skip' && c.status !== 'na')
    .map(c => `
      <tr>
        <td style="padding: 8px 12px; border-bottom: 1px solid #f3f4f6; font-size: 14px;">
          ${statusIcon(c.status)} ${c.name}
        </td>
        <td style="padding: 8px 12px; border-bottom: 1px solid #f3f4f6; font-size: 13px; color: #6b7280;">
          ${c.description}
        </td>
      </tr>
    `).join('');

  return {
    subject: `Your Compliance Report — ${data.score}/100 — ${data.url}`,
    html: `
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f9fafb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <div style="max-width: 600px; margin: 0 auto; padding: 24px 16px;">

    <!-- Header -->
    <div style="text-align: center; padding: 24px 0 16px;">
      <h1 style="margin: 0; font-size: 22px; color: #1a2744; font-weight: 800;">ComplyWithJudy</h1>
      <p style="margin: 4px 0 0; font-size: 13px; color: #9ca3af;">California Real Estate & Mortgage Compliance Scanner</p>
    </div>

    <!-- Main card -->
    <div style="background: #ffffff; border-radius: 16px; border: 1px solid #e5e7eb; overflow: hidden;">

      <!-- Score banner -->
      <div style="background: ${color}; padding: 32px 24px; text-align: center;">
        <div style="font-size: 56px; font-weight: 800; color: #ffffff; line-height: 1;">${data.score}</div>
        <div style="font-size: 14px; color: rgba(255,255,255,0.85); margin-top: 4px;">/100 — ${label}</div>
      </div>

      <!-- URL + profession -->
      <div style="padding: 20px 24px; border-bottom: 1px solid #f3f4f6;">
        <p style="margin: 0; font-size: 15px; font-weight: 600; color: #111827; word-break: break-all;">${data.url}</p>
        <p style="margin: 4px 0 0; font-size: 13px; color: #9ca3af;">${professionLabel(data.profession)}</p>
      </div>

      <!-- Summary stats -->
      <div style="display: flex; padding: 16px 24px; border-bottom: 1px solid #f3f4f6;">
        <div style="flex: 1; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #16a34a;">${data.passed}</div>
          <div style="font-size: 11px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em;">Passed</div>
        </div>
        <div style="flex: 1; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #d97706;">${data.warnings}</div>
          <div style="font-size: 11px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em;">Warnings</div>
        </div>
        <div style="flex: 1; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #dc2626;">${data.failed}</div>
          <div style="font-size: 11px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em;">Failed</div>
        </div>
      </div>

      <!-- Check results -->
      <div style="padding: 16px 24px;">
        <h3 style="margin: 0 0 12px; font-size: 14px; color: #374151; font-weight: 600;">${data.totalChecks} Checks Performed</h3>
        <table style="width: 100%; border-collapse: collapse;">
          ${checkRows}
        </table>
      </div>

      <!-- CTA -->
      <div style="padding: 24px; text-align: center; border-top: 1px solid #f3f4f6;">
        <a href="${resultsUrl}" style="display: inline-block; background: #e8821a; color: #ffffff; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 12px; text-decoration: none;">
          View Full Report
        </a>
        ${!data.isPaid ? `
        <p style="margin: 16px 0 0; font-size: 13px; color: #9ca3af;">
          Upgrade from <strong>$19.99</strong> to unlock fix instructions, regulation citations, and webmaster email templates.
        </p>
        ` : ''}
      </div>
    </div>

    <!-- Footer -->
    <div style="text-align: center; padding: 24px 0;">
      <p style="margin: 0; font-size: 12px; color: #9ca3af;">
        <a href="https://complywithjudy.com" style="color: #1a2744; text-decoration: none; font-weight: 600;">ComplyWithJudy.com</a>
        — Built by a California DRE Broker
      </p>
      <p style="margin: 8px 0 0; font-size: 11px; color: #d1d5db;">
        The Colyer Team
      </p>
    </div>
  </div>
</body>
</html>
    `.trim(),
  };
}

function buildPaidEmail(data: ScanEmailData): { subject: string; html: string } {
  const color = scoreColor(data.score);
  const label = scoreLabel(data.score);
  const resultsUrl = `https://complywithjudy.com/results/${data.scanId}`;

  const failedChecks = (data.checks || []).filter(c => c.status === 'fail' || c.status === 'warn');

  const fixSections = failedChecks.map(c => `
    <div style="margin-bottom: 16px; padding: 16px; background: ${c.status === 'fail' ? '#fef2f2' : '#fffbeb'}; border-radius: 12px; border-left: 4px solid ${c.status === 'fail' ? '#dc2626' : '#d97706'};">
      <p style="margin: 0 0 6px; font-size: 14px; font-weight: 600; color: #111827;">
        ${statusIcon(c.status)} ${c.name}
      </p>
      <p style="margin: 0 0 8px; font-size: 13px; color: #6b7280;">${c.description}</p>
      ${c.fix ? `
      <div style="background: #ffffff; padding: 12px; border-radius: 8px; border: 1px solid #e5e7eb;">
        <p style="margin: 0 0 4px; font-size: 11px; color: #9ca3af; text-transform: uppercase; font-weight: 600; letter-spacing: 0.05em;">How to fix</p>
        <p style="margin: 0; font-size: 13px; color: #374151; white-space: pre-line;">${c.fix}</p>
      </div>
      ` : ''}
    </div>
  `).join('');

  return {
    subject: `Your Compliance Report — ${data.score}/100 — ${data.url}`,
    html: `
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f9fafb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <div style="max-width: 600px; margin: 0 auto; padding: 24px 16px;">

    <!-- Header -->
    <div style="text-align: center; padding: 24px 0 16px;">
      <h1 style="margin: 0; font-size: 22px; color: #1a2744; font-weight: 800;">ComplyWithJudy</h1>
      <p style="margin: 4px 0 0; font-size: 13px; color: #9ca3af;">Your Full Compliance Report</p>
    </div>

    <!-- Main card -->
    <div style="background: #ffffff; border-radius: 16px; border: 1px solid #e5e7eb; overflow: hidden;">

      <!-- Score banner -->
      <div style="background: ${color}; padding: 32px 24px; text-align: center;">
        <div style="font-size: 56px; font-weight: 800; color: #ffffff; line-height: 1;">${data.score}</div>
        <div style="font-size: 14px; color: rgba(255,255,255,0.85); margin-top: 4px;">/100 — ${label}</div>
      </div>

      <!-- URL -->
      <div style="padding: 20px 24px; border-bottom: 1px solid #f3f4f6;">
        <p style="margin: 0; font-size: 15px; font-weight: 600; color: #111827; word-break: break-all;">${data.url}</p>
        <p style="margin: 4px 0 0; font-size: 13px; color: #9ca3af;">${professionLabel(data.profession)}</p>
      </div>

      <!-- Summary -->
      <div style="display: flex; padding: 16px 24px; border-bottom: 1px solid #f3f4f6;">
        <div style="flex: 1; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #16a34a;">${data.passed}</div>
          <div style="font-size: 11px; color: #9ca3af; text-transform: uppercase;">Passed</div>
        </div>
        <div style="flex: 1; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #d97706;">${data.warnings}</div>
          <div style="font-size: 11px; color: #9ca3af; text-transform: uppercase;">Warnings</div>
        </div>
        <div style="flex: 1; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #dc2626;">${data.failed}</div>
          <div style="font-size: 11px; color: #9ca3af; text-transform: uppercase;">Failed</div>
        </div>
      </div>

      <!-- Fix instructions -->
      ${failedChecks.length > 0 ? `
      <div style="padding: 24px;">
        <h3 style="margin: 0 0 16px; font-size: 16px; color: #111827; font-weight: 700;">
          ${failedChecks.length} Item${failedChecks.length > 1 ? 's' : ''} to Fix
        </h3>
        ${fixSections}
      </div>
      ` : `
      <div style="padding: 24px; text-align: center;">
        <p style="font-size: 15px; color: #16a34a; font-weight: 600;">🎉 All checks passed! Your site looks great.</p>
      </div>
      `}

      <!-- CTA -->
      <div style="padding: 24px; text-align: center; border-top: 1px solid #f3f4f6;">
        <a href="${resultsUrl}" style="display: inline-block; background: #e8821a; color: #ffffff; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 12px; text-decoration: none;">
          View Interactive Report
        </a>
        <p style="margin: 16px 0 0; font-size: 13px; color: #9ca3af;">
          Your full report includes webmaster email templates and regulation source links.
        </p>
      </div>
    </div>

    <!-- Footer -->
    <div style="text-align: center; padding: 24px 0;">
      <p style="margin: 0; font-size: 12px; color: #9ca3af;">
        <a href="https://complywithjudy.com" style="color: #1a2744; text-decoration: none; font-weight: 600;">ComplyWithJudy.com</a>
        — Built by a California DRE Broker
      </p>
      <p style="margin: 8px 0 0; font-size: 11px; color: #d1d5db;">
        The Colyer Team
      </p>
    </div>
  </div>
</body>
</html>
    `.trim(),
  };
}

// ----------------------------------------------------------------
// Courtesy scan email (full report sent by admin to a partner)
// ----------------------------------------------------------------

interface CourtesyEmailData {
  to: string;
  toName: string;
  scanId: string;
  url: string;
  score: number;
  profession: string;
  passed: number;
  warnings: number;
  failed: number;
  totalChecks: number;
  checks?: {
    name: string;
    status: string;
    description: string;
    fix?: string;
  }[];
}

function buildCourtesyEmail(data: CourtesyEmailData): { subject: string; html: string } {
  const color = scoreColor(data.score);
  const label = scoreLabel(data.score);
  const resultsUrl = `https://complywithjudy.com/results/${data.scanId}`;
  const greeting = data.toName ? `Hi ${data.toName},` : 'Hi there,';

  const failedChecks = (data.checks || []).filter(c => c.status === 'fail' || c.status === 'warn');

  const fixSections = failedChecks.map(c => `
    <div style="margin-bottom: 16px; padding: 16px; background: ${c.status === 'fail' ? '#fef2f2' : '#fffbeb'}; border-radius: 12px; border-left: 4px solid ${c.status === 'fail' ? '#dc2626' : '#d97706'};">
      <p style="margin: 0 0 6px; font-size: 14px; font-weight: 600; color: #111827;">
        ${statusIcon(c.status)} ${c.name}
      </p>
      <p style="margin: 0 0 8px; font-size: 13px; color: #6b7280;">${c.description}</p>
      ${c.fix ? `
      <div style="background: #ffffff; padding: 12px; border-radius: 8px; border: 1px solid #e5e7eb;">
        <p style="margin: 0 0 4px; font-size: 11px; color: #9ca3af; text-transform: uppercase; font-weight: 600; letter-spacing: 0.05em;">How to fix</p>
        <p style="margin: 0; font-size: 13px; color: #374151; white-space: pre-line;">${c.fix}</p>
      </div>
      ` : ''}
    </div>
  `).join('');

  const allCheckRows = (data.checks || [])
    .filter(c => c.status !== 'skip' && c.status !== 'na')
    .map(c => `
      <tr>
        <td style="padding: 8px 12px; border-bottom: 1px solid #f3f4f6; font-size: 14px;">
          ${statusIcon(c.status)} ${c.name}
        </td>
        <td style="padding: 8px 12px; border-bottom: 1px solid #f3f4f6; font-size: 13px; color: #6b7280;">
          ${c.description}
        </td>
      </tr>
    `).join('');

  return {
    subject: `Courtesy Compliance Report for ${data.url} — ${data.score}/100`,
    html: `
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f9fafb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
  <div style="max-width: 600px; margin: 0 auto; padding: 24px 16px;">

    <!-- Header -->
    <div style="text-align: center; padding: 24px 0 16px;">
      <h1 style="margin: 0; font-size: 22px; color: #1a2744; font-weight: 800;">ComplyWithJudy</h1>
      <p style="margin: 4px 0 0; font-size: 13px; color: #9ca3af;">California Real Estate & Mortgage Compliance Scanner</p>
    </div>

    <!-- Personal message -->
    <div style="background: #ffffff; border-radius: 16px; border: 1px solid #e5e7eb; padding: 24px; margin-bottom: 16px;">
      <p style="margin: 0 0 12px; font-size: 15px; color: #111827; line-height: 1.6;">
        ${greeting}
      </p>
      <p style="margin: 0 0 12px; font-size: 15px; color: #374151; line-height: 1.6;">
        I ran a complimentary compliance scan on your website as a courtesy. California's DRE, NMLS, and federal advertising rules have specific requirements for ${data.profession === 'lending' ? 'mortgage lender' : 'real estate'} websites — and many agents don't realize their site may have issues.
      </p>
      <p style="margin: 0; font-size: 15px; color: #374151; line-height: 1.6;">
        Below is your full report with your score, what passed, what needs attention, and exactly how to fix each issue.
      </p>
    </div>

    <!-- Main card -->
    <div style="background: #ffffff; border-radius: 16px; border: 1px solid #e5e7eb; overflow: hidden;">

      <!-- Score banner -->
      <div style="background: ${color}; padding: 32px 24px; text-align: center;">
        <div style="font-size: 56px; font-weight: 800; color: #ffffff; line-height: 1;">${data.score}</div>
        <div style="font-size: 14px; color: rgba(255,255,255,0.85); margin-top: 4px;">/100 — ${label}</div>
      </div>

      <!-- URL + profession -->
      <div style="padding: 20px 24px; border-bottom: 1px solid #f3f4f6;">
        <p style="margin: 0; font-size: 15px; font-weight: 600; color: #111827; word-break: break-all;">${data.url}</p>
        <p style="margin: 4px 0 0; font-size: 13px; color: #9ca3af;">${professionLabel(data.profession)}</p>
      </div>

      <!-- Summary stats -->
      <div style="display: flex; padding: 16px 24px; border-bottom: 1px solid #f3f4f6;">
        <div style="flex: 1; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #16a34a;">${data.passed}</div>
          <div style="font-size: 11px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em;">Passed</div>
        </div>
        <div style="flex: 1; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #d97706;">${data.warnings}</div>
          <div style="font-size: 11px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em;">Warnings</div>
        </div>
        <div style="flex: 1; text-align: center;">
          <div style="font-size: 24px; font-weight: 700; color: #dc2626;">${data.failed}</div>
          <div style="font-size: 11px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em;">Failed</div>
        </div>
      </div>

      <!-- All checks -->
      <div style="padding: 16px 24px; border-bottom: 1px solid #f3f4f6;">
        <h3 style="margin: 0 0 12px; font-size: 14px; color: #374151; font-weight: 600;">${data.totalChecks} Checks Performed</h3>
        <table style="width: 100%; border-collapse: collapse;">
          ${allCheckRows}
        </table>
      </div>

      <!-- Fix instructions (unblurred — this is the value prop) -->
      ${failedChecks.length > 0 ? `
      <div style="padding: 24px;">
        <h3 style="margin: 0 0 16px; font-size: 16px; color: #111827; font-weight: 700;">
          ${failedChecks.length} Item${failedChecks.length > 1 ? 's' : ''} to Fix
        </h3>
        ${fixSections}
      </div>
      ` : `
      <div style="padding: 24px; text-align: center;">
        <p style="font-size: 15px; color: #16a34a; font-weight: 600;">All checks passed! Your site looks great.</p>
      </div>
      `}

      <!-- CTA -->
      <div style="padding: 24px; text-align: center; border-top: 1px solid #f3f4f6;">
        <a href="${resultsUrl}" style="display: inline-block; background: #e8821a; color: #ffffff; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 12px; text-decoration: none;">
          View Interactive Report
        </a>
      </div>
    </div>

    <!-- Sign up pitch -->
    <div style="background: #ffffff; border-radius: 16px; border: 1px solid #e5e7eb; padding: 24px; margin-top: 16px;">
      <h3 style="margin: 0 0 12px; font-size: 16px; color: #1a2744; font-weight: 700;">Want to stay compliant?</h3>
      <p style="margin: 0 0 16px; font-size: 14px; color: #374151; line-height: 1.6;">
        Regulations change, websites get updated, and new rules take effect. ComplyWithJudy monitors your site and alerts you when something falls out of compliance — so you never have to worry about a DRE audit catching you off guard.
      </p>
      <ul style="margin: 0 0 16px; padding-left: 20px; font-size: 14px; color: #374151; line-height: 1.8;">
        <li>Unlimited scans with full fix instructions</li>
        <li>Webmaster-ready email templates for every violation</li>
        <li>Regulation citations with source links</li>
        <li>Plans start at <strong>$29.99/year</strong></li>
      </ul>
      <div style="text-align: center;">
        <a href="https://complywithjudy.com/#pricing" style="display: inline-block; background: #1a2744; color: #ffffff; font-weight: 700; font-size: 14px; padding: 12px 28px; border-radius: 12px; text-decoration: none;">
          View Plans & Pricing
        </a>
      </div>
    </div>

    <!-- Personal sign-off -->
    <div style="background: #ffffff; border-radius: 16px; border: 1px solid #e5e7eb; padding: 24px; margin-top: 16px;">
      <p style="margin: 0 0 8px; font-size: 14px; color: #374151; line-height: 1.6;">
        If you found this report valuable, I'd love to hear your feedback. And if you know other agents or loan officers who could benefit, feel free to forward this along.
      </p>
      <p style="margin: 0; font-size: 14px; color: #374151; line-height: 1.6;">
        — Michael Colyer<br>
        <span style="color: #9ca3af;">DRE #01842442 · NMLS #276626</span><br>
        <a href="mailto:mike@thecolyerteam.com" style="color: #1a2744;">mike@thecolyerteam.com</a> · <a href="tel:6502888170" style="color: #1a2744;">(650) 288-8170</a>
      </p>
    </div>

    <!-- Footer -->
    <div style="text-align: center; padding: 24px 0;">
      <p style="margin: 0; font-size: 12px; color: #9ca3af;">
        <a href="https://complywithjudy.com" style="color: #1a2744; text-decoration: none; font-weight: 600;">ComplyWithJudy.com</a>
        — Built by a California DRE Broker
      </p>
      <p style="margin: 8px 0 0; font-size: 11px; color: #d1d5db;">
        The Colyer Team
      </p>
    </div>
  </div>
</body>
</html>
    `.trim(),
  };
}

// ----------------------------------------------------------------
// Route: POST /courtesy-scan
// ----------------------------------------------------------------
async function handleCourtesyScan(body: CourtesyEmailData) {
  if (!body.to || !body.scanId) {
    return { statusCode: 400, headers: CORS_HEADERS, body: JSON.stringify({ error: 'Missing to or scanId' }) };
  }

  const emailContent = buildCourtesyEmail(body);

  try {
    await transporter.sendMail({
      from: `"Michael Colyer via ComplyWithJudy" <${GMAIL_USER}>`,
      replyTo: 'mike@thecolyerteam.com',
      to: body.to,
      subject: emailContent.subject,
      html: emailContent.html,
    });

    return {
      statusCode: 200,
      headers: CORS_HEADERS,
      body: JSON.stringify({ ok: true, message: 'Courtesy email sent' }),
    };
  } catch (err) {
    console.error('Courtesy email send failed:', err);
    return {
      statusCode: 500,
      headers: CORS_HEADERS,
      body: JSON.stringify({ error: 'Failed to send courtesy email', detail: String(err) }),
    };
  }
}

// ----------------------------------------------------------------
// Route: POST /scan-complete
// ----------------------------------------------------------------
async function handleScanComplete(body: ScanEmailData) {
  if (!body.to || !body.scanId) {
    return { statusCode: 400, headers: CORS_HEADERS, body: JSON.stringify({ error: 'Missing to or scanId' }) };
  }

  const emailContent = body.isPaid ? buildPaidEmail(body) : buildFreeEmail(body);

  try {
    await transporter.sendMail({
      from: `"Judy from ComplyWithJudy" <${GMAIL_USER}>`,
      to: body.to,
      subject: emailContent.subject,
      html: emailContent.html,
    });

    return {
      statusCode: 200,
      headers: CORS_HEADERS,
      body: JSON.stringify({ ok: true, message: 'Email sent' }),
    };
  } catch (err) {
    console.error('Email send failed:', err);
    return {
      statusCode: 500,
      headers: CORS_HEADERS,
      body: JSON.stringify({ error: 'Failed to send email', detail: String(err) }),
    };
  }
}

// ----------------------------------------------------------------
// Main handler
// ----------------------------------------------------------------
const handler: Handler = async (event) => {
  if (event.httpMethod === 'OPTIONS') {
    return { statusCode: 204, headers: CORS_HEADERS, body: '' };
  }

  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, headers: CORS_HEADERS, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  const path = event.path.split('/').pop() || '';

  try {
    switch (path) {
      case 'scan-complete': {
        const body = JSON.parse(event.body || '{}');
        return handleScanComplete(body);
      }
      case 'courtesy-scan': {
        const body = JSON.parse(event.body || '{}');
        return handleCourtesyScan(body);
      }
      default:
        return { statusCode: 404, headers: CORS_HEADERS, body: JSON.stringify({ error: `Unknown route: ${path}` }) };
    }
  } catch (err) {
    console.error('Email function error:', err);
    return {
      statusCode: 500,
      headers: CORS_HEADERS,
      body: JSON.stringify({ error: 'Internal server error' }),
    };
  }
};

export { handler };
