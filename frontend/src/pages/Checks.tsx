import { Link } from 'react-router-dom'
import Navbar from '../components/Navbar'
import { useAuth } from '../App'

interface CheckItem {
  name: string
  description: string
  severity: 'critical' | 'required' | 'recommended'
}

interface CheckCategory {
  title: string
  icon: string
  profession: 'realestate' | 'lending' | 'both'
  checks: CheckItem[]
}

const CATEGORIES: CheckCategory[] = [
  {
    title: 'DRE License & Broker Disclosure',
    icon: '🏛️',
    profession: 'realestate',
    checks: [
      { name: 'DRE license number present', description: 'Your DRE license number must appear on every page of advertising material.', severity: 'critical' },
      { name: 'Responsible broker name displayed', description: 'The supervising or responsible broker must be identified on the site.', severity: 'critical' },
      { name: 'Broker DRE number displayed', description: 'The brokerage DRE number must accompany the broker name.', severity: 'critical' },
      { name: 'License number format correct', description: 'DRE numbers must be in the format DRE #XXXXXXXX, not abbreviated or partial.', severity: 'required' },
      { name: 'Individual vs. team distinction', description: 'If operating as a team, the relationship to the responsible broker must be clear.', severity: 'required' },
    ],
  },
  {
    title: 'Team Advertising Rules',
    icon: '👥',
    profession: 'realestate',
    checks: [
      { name: 'Team name includes broker affiliation', description: 'A team operating under a brokerage must display the broker name at least as prominently as the team name.', severity: 'critical' },
      { name: 'No misleading team name', description: "Team names cannot imply the team is a licensed brokerage if it isn't.", severity: 'critical' },
      { name: 'Individual agent disclosures', description: 'Each team member advertising individually must include their own DRE number.', severity: 'required' },
    ],
  },
  {
    title: 'Equal Housing & Fair Housing',
    icon: '🏠',
    profession: 'both',
    checks: [
      { name: 'Equal Housing Opportunity logo or statement', description: 'The Equal Housing Opportunity logo or statement is required on all advertising.', severity: 'critical' },
      { name: 'Equal Housing Lender (MLOs)', description: 'Mortgage advertisers must display "Equal Housing Lender" specifically.', severity: 'critical' },
      { name: 'No prohibited fair housing language', description: 'Advertising cannot include language that indicates preference or limitation based on protected class.', severity: 'critical' },
    ],
  },
  {
    title: 'AB 723 — Digitally Altered Images',
    icon: '📸',
    profession: 'realestate',
    checks: [
      { name: 'Disclosure for virtually staged photos', description: 'California AB 723 requires disclosure when listing photos are virtually staged.', severity: 'critical' },
      { name: 'Disclosure for AI-generated images', description: 'AI-generated or significantly digitally altered property images require disclosure.', severity: 'critical' },
      { name: 'Disclosure proximity', description: 'The disclosure must appear in proximity to the affected image.', severity: 'required' },
    ],
  },
  {
    title: 'Virtual Office Advertisement (VOA)',
    icon: '💻',
    profession: 'realestate',
    checks: [
      { name: 'VOA identified as such', description: 'A virtual office website must clearly identify itself as a real estate website.', severity: 'critical' },
      { name: 'Broker name and license on VOA', description: 'Virtual office sites require the same broker disclosures as physical offices.', severity: 'critical' },
      { name: 'MLS compliance for VOA listings', description: 'VOA operators must comply with their MLS rules for displaying listings.', severity: 'required' },
    ],
  },
  {
    title: 'Property Listings & Captions',
    icon: '📋',
    profession: 'realestate',
    checks: [
      { name: 'Property photo captions compliant', description: 'Captions for property images must not contain misleading information.', severity: 'required' },
      { name: 'Price display rules', description: 'Sold prices and list prices must be accurately displayed and not manipulated.', severity: 'required' },
      { name: 'MLS attribution', description: 'IDX listings must include required MLS attribution and copyright notices.', severity: 'required' },
      { name: 'Reciprocal links', description: 'IDX sites must include required reciprocal MLS links.', severity: 'recommended' },
    ],
  },
  {
    title: 'SAFE Act & NMLS — Mortgage',
    icon: '📋',
    profession: 'lending',
    checks: [
      { name: 'Individual MLO NMLS ID present', description: 'Every licensed MLO must display their individual NMLS ID number on advertising.', severity: 'critical' },
      { name: 'Company NMLS ID present', description: 'The licensed company NMLS ID must also appear.', severity: 'critical' },
      { name: 'NMLS ID on every page', description: "NMLS IDs must appear on every page, not just the 'About' page.", severity: 'required' },
      { name: 'NMLS Consumer Access link', description: 'Linking to the NMLS Consumer Access lookup is best practice and required by some states.', severity: 'recommended' },
    ],
  },
  {
    title: 'TILA / Reg Z — Truth in Lending',
    icon: '⚖️',
    profession: 'lending',
    checks: [
      { name: 'APR disclosed near triggering terms', description: 'Any mention of a specific rate, monthly payment, or loan term is a "triggering term" requiring APR disclosure within close proximity.', severity: 'critical' },
      { name: 'APR display prominence', description: 'The APR must be at least as prominent as the triggering rate.', severity: 'critical' },
      { name: 'Variable rate disclosure', description: 'If advertised rates are variable, the ad must state that the rate may increase.', severity: 'critical' },
      { name: 'Loan term disclosure', description: 'If a specific loan term triggers Reg Z, all required terms must be disclosed.', severity: 'required' },
      { name: 'Example disclosures complete', description: 'Any example loan calculation must include all Reg Z required terms.', severity: 'required' },
    ],
  },
  {
    title: 'DFPI — CA Dept. of Financial Protection',
    icon: '🏦',
    profession: 'lending',
    checks: [
      { name: 'No guaranteed approval claims', description: '"Guaranteed approval" and similar claims are prohibited under DFPI advertising rules.', severity: 'critical' },
      { name: 'No "no credit check" claims', description: 'Claims of no credit check for mortgage products are prohibited.', severity: 'critical' },
      { name: 'Licensed lender identification', description: 'The site must identify the licensed entity name as registered with DFPI.', severity: 'required' },
      { name: 'No prohibited rate lock claims', description: 'Rate lock advertising must follow DFPI guidance on accuracy and timing.', severity: 'required' },
    ],
  },
  {
    title: 'Privacy & Data',
    icon: '🔒',
    profession: 'both',
    checks: [
      { name: 'CCPA privacy policy present', description: 'California businesses collecting consumer data must have a CCPA-compliant privacy policy.', severity: 'critical' },
      { name: 'Privacy policy accessible from footer', description: 'The privacy policy link must be easy to find, typically in the footer.', severity: 'required' },
      { name: '"Do Not Sell" option (if applicable)', description: 'If you sell or share consumer personal information, a "Do Not Sell or Share" link is required.', severity: 'required' },
      { name: 'Contact form data disclosure', description: 'Forms collecting personal information must disclose how that data is used.', severity: 'required' },
    ],
  },
  {
    title: 'Contact & Accessibility',
    icon: '📞',
    profession: 'both',
    checks: [
      { name: 'Contact information present', description: 'A phone number or email address must be accessible on the site.', severity: 'required' },
      { name: 'Physical or mailing address', description: 'DRE requires a business address be available; P.O. Box alone is insufficient for some contexts.', severity: 'required' },
      { name: 'ADA accessibility statement', description: 'An accessibility statement demonstrates good faith compliance with ADA Title III.', severity: 'recommended' },
      { name: 'WCAG 2.1 AA basics', description: 'Image alt text, sufficient color contrast, and keyboard navigation are basic accessibility requirements.', severity: 'recommended' },
    ],
  },
]

const SEVERITY_CONFIG = {
  critical: { bg: 'bg-red-50', text: 'text-red-800', badge: 'bg-red-100 text-red-700', label: 'Critical' },
  required: { bg: 'bg-amber-50', text: 'text-amber-800', badge: 'bg-amber-100 text-amber-700', label: 'Required' },
  recommended: { bg: 'bg-green-50', text: 'text-green-800', badge: 'bg-green-100 text-green-700', label: 'Best practice' },
}

const PROF_CONFIG = {
  both: { bg: 'bg-indigo-100', text: 'text-indigo-800', label: 'All' },
  lending: { bg: 'bg-blue-100', text: 'text-blue-800', label: 'MLO' },
  realestate: { bg: 'bg-amber-100', text: 'text-amber-800', label: 'RE Agent' },
}

export default function Checks() {
  const { user: _user } = useAuth()

  const reCount = CATEGORIES
    .filter(c => c.profession !== 'lending')
    .reduce((n, c) => n + c.checks.length, 0)
  const mlCount = CATEGORIES
    .filter(c => c.profession !== 'realestate')
    .reduce((n, c) => n + c.checks.length, 0)

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />

      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-10 sm:py-14">

        {/* Header */}
        <div className="text-center mb-10">
          <h1 className="text-2xl sm:text-3xl font-extrabold text-gray-900 mb-2">
            Every check we run — and why it matters
          </h1>
          <p className="text-gray-500 max-w-xl mx-auto mb-6">
            Built on actual California DRE enforcement guidance, CFPB regulations, and NMLS requirements.
            Not generic checklists — rules your license depends on.
          </p>
          <div className="flex justify-center gap-8 flex-wrap">
            <div className="text-center">
              <div className="text-3xl font-extrabold text-brand-gold">{reCount}+</div>
              <div className="text-xs text-gray-500">Real estate checks</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-extrabold text-brand-gold">{mlCount}+</div>
              <div className="text-xs text-gray-500">MLO / lending checks</div>
            </div>
          </div>
        </div>

        {/* Categories */}
        <div className="space-y-8">
          {CATEGORIES.map(cat => {
            const prof = PROF_CONFIG[cat.profession]
            return (
              <div key={cat.title}>
                <div className="flex items-center gap-2.5 mb-3">
                  <span className="text-xl">{cat.icon}</span>
                  <h2 className="text-base font-bold text-gray-900">{cat.title}</h2>
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${prof.bg} ${prof.text}`}>
                    {prof.label}
                  </span>
                </div>

                <div className="space-y-1.5">
                  {cat.checks.map(check => {
                    const sev = SEVERITY_CONFIG[check.severity]
                    return (
                      <div key={check.name} className="bg-white border border-gray-200 rounded-lg p-3.5 flex gap-3 items-start">
                        <span className={`text-xs font-bold px-2 py-0.5 rounded-full whitespace-nowrap mt-0.5 flex-shrink-0 ${sev.badge}`}>
                          {sev.label}
                        </span>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">{check.name}</div>
                          <div className="text-gray-500 text-sm mt-0.5 leading-relaxed">{check.description}</div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>

        {/* CTA */}
        <div className="bg-brand-blue rounded-2xl p-8 text-center mt-10">
          <h3 className="text-white font-bold text-lg mb-2">Ready to find out where you stand?</h3>
          <p className="text-blue-200 text-sm mb-5">
            First scan is free. No credit card required.
          </p>
          <Link
            to="/scan"
            className="inline-block bg-brand-gold hover:bg-brand-gold-dark text-white font-bold px-8 py-3.5 rounded-xl transition-colors shadow"
          >
            Scan My Website Free →
          </Link>
        </div>
      </div>
    </div>
  )
}
