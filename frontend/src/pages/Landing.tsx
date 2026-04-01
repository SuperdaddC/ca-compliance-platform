import { useState } from 'react'
import { Link } from 'react-router-dom'
import Navbar from '../components/Navbar'
import { useAuth } from '../App'
import { PLANS, createCheckout } from '../lib/api'
import type { PlanKey } from '../lib/api'

export default function Landing() {
  const { user } = useAuth()
  const [showPrivacy, setShowPrivacy] = useState(false)
  const [showDns, setShowDns] = useState(false)
  const [showAda, setShowAda] = useState(false)

  async function handlePlanClick(plan: PlanKey) {
    try {
      const url = await createCheckout({
        plan,
        email: user?.email,
        userId: user?.id,
      })
      window.location.href = url
    } catch {
      alert('Could not start checkout. Please try again.')
    }
  }

  return (
    <div className="min-h-screen bg-white">
      <Navbar />

      {/* Hero */}
      <div className="bg-brand-blue text-white py-20 px-4">
        <div className="max-w-3xl mx-auto text-center">
          <h1 className="text-4xl sm:text-5xl font-extrabold mb-4 leading-tight">
            Is your California real estate website compliant?
          </h1>
          <p className="text-blue-200 text-lg mb-8 max-w-xl mx-auto">
            Instant compliance audit against DRE, NMLS, DFPI, and federal advertising regulations.
            Find violations before the regulators do.
          </p>
          <div className="flex justify-center gap-6 mb-10 flex-wrap">
            <div className="text-center">
              <div className="text-3xl font-extrabold text-brand-gold">40+</div>
              <div className="text-sm text-blue-200">Checks performed</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-extrabold text-brand-gold">60s</div>
              <div className="text-sm text-blue-200">Instant results</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-extrabold text-brand-gold">28 yrs</div>
              <div className="text-sm text-blue-200">DRE experience</div>
            </div>
          </div>
          <Link
            to="/scan"
            className="inline-block bg-brand-gold hover:bg-brand-gold-dark text-white font-bold text-lg px-8 py-4 rounded-xl transition-colors shadow-lg"
          >
            Scan My Website Free →
          </Link>
          <p className="text-xs text-blue-300 mt-3">First scan is always free. No credit card required.</p>
        </div>
      </div>

      {/* Features */}
      <div className="max-w-5xl mx-auto px-4 py-16">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-8">
          {[
            { icon: '⚡', title: 'Instant Results', desc: 'Get a complete compliance report in under 60 seconds.' },
            { icon: '📋', title: 'California-Specific', desc: 'Built for CA DRE and DFPI regulations, including AB 723.' },
            { icon: '🔧', title: 'Actionable Fixes', desc: "Don't just find violations — get plain-language guidance to fix them." },
            { icon: '📖', title: 'Cited Sources', desc: 'Every check links to official regulations from CFPB, DRE, and CA Legislature.' },
            { icon: '🛡️', title: 'Stay Protected', desc: 'Avoid DRE enforcement actions and fines with proactive compliance.' },
            { icon: '💼', title: 'Built by a Broker', desc: 'Created by Michael Colyer, DRE Broker with 28 years of experience.' },
          ].map((f) => (
            <div key={f.title} className="bg-gray-50 rounded-xl p-6 border border-gray-100">
              <div className="text-2xl mb-3">{f.icon}</div>
              <h3 className="font-bold text-gray-900 mb-1">{f.title}</h3>
              <p className="text-sm text-gray-500">{f.desc}</p>
            </div>
          ))}
        </div>
      </div>

      {/* See all checks */}
      <div className="text-center py-4">
        <Link to="/checks" className="text-brand-gold font-semibold hover:underline">
          See all 40+ checks we run →
        </Link>
      </div>

      {/* Pricing */}
      <div id="pricing" className="bg-gray-50 py-16 px-4">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-2xl font-bold text-center text-gray-900 mb-10">Simple, Transparent Pricing</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {(Object.keys(PLANS) as PlanKey[]).map((key) => {
              const plan = PLANS[key]
              return (
                <div
                  key={key}
                  className={`bg-white rounded-xl p-6 border-2 ${
                    plan.highlight ? 'border-brand-gold shadow-md' : 'border-gray-200'
                  }`}
                >
                  <h3 className="font-bold text-gray-900 mb-1">{plan.name}</h3>
                  <div className="mb-3">
                    <span className="text-3xl font-extrabold text-brand-blue">{plan.price}</span>
                    <span className="text-sm text-gray-500">{plan.period}</span>
                  </div>
                  <p className="text-sm text-gray-500 mb-3">{plan.scans}</p>
                  <p className="text-xs text-gray-400 mb-4">{plan.description}</p>
                  <button
                    onClick={() => handlePlanClick(key)}
                    className={`w-full py-2 rounded-lg font-semibold text-sm transition-colors ${
                      plan.highlight
                        ? 'bg-brand-gold hover:bg-brand-gold-dark text-white'
                        : 'bg-brand-blue hover:bg-blue-800 text-white'
                    }`}
                  >
                    {key === 'single' ? 'Buy Now' : 'Get Started'}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer className="bg-brand-blue text-white py-10 px-4 text-center text-sm">
        <p className="font-semibold">The Colyer Team</p>
        <p className="text-blue-300 mt-1">Michael Colyer, NMLS #276626, DRE #01842442</p>
        <p className="text-blue-300">2214 Faraday Ave, Carlsbad, CA 92008 · <a href="tel:6502888170" className="text-blue-300 hover:text-white">(650) 288-8170</a> · <a href="mailto:mike@thecolyerteam.com" className="text-blue-300 hover:text-white">mike@thecolyerteam.com</a></p>
        <div className="flex flex-wrap justify-center gap-4 mt-4 text-xs text-blue-400">
          <button onClick={() => setShowPrivacy(true)} className="hover:text-white underline bg-transparent border-0 cursor-pointer text-blue-400 text-xs">Privacy Policy</button>
          <button onClick={() => setShowDns(true)} className="hover:text-white underline bg-transparent border-0 cursor-pointer text-blue-400 text-xs">Do Not Sell or Share My Personal Information</button>
          <button onClick={() => setShowAda(true)} className="hover:text-white underline bg-transparent border-0 cursor-pointer text-blue-400 text-xs">Accessibility</button>
        </div>
        <p className="text-blue-400 mt-4 text-xs">© 2026 The Colyer Team LLC</p>
      </footer>

      {/* Privacy Policy Modal */}
      {showPrivacy && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setShowPrivacy(false)}>
          <div className="bg-white text-gray-800 rounded-xl max-w-lg w-full max-h-[80vh] overflow-y-auto p-6" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-lg font-bold">Privacy Policy</h2>
              <button onClick={() => setShowPrivacy(false)} className="text-gray-400 hover:text-gray-600 text-2xl">&times;</button>
            </div>
            <p className="text-sm text-gray-500 mb-3"><strong>Effective Date:</strong> April 1, 2026</p>
            <p className="text-sm mb-3">The Colyer Team ("we," "us") operates ComplyWithJudy.com, a compliance scanning service. We are committed to protecting your privacy.</p>
            <h3 className="font-semibold text-sm mt-3 mb-1">Information We Collect</h3>
            <ul className="text-sm list-disc pl-5 space-y-1">
              <li>Account information (name, email)</li>
              <li>Website URLs you submit for scanning</li>
              <li>Usage data (pages visited, features used)</li>
              <li>Payment information (processed securely by Stripe)</li>
            </ul>
            <h3 className="font-semibold text-sm mt-3 mb-1">How We Use Your Information</h3>
            <ul className="text-sm list-disc pl-5 space-y-1">
              <li>To provide compliance scanning and reporting services</li>
              <li>To send scan results and alerts</li>
              <li>To improve our services</li>
            </ul>
            <h3 className="font-semibold text-sm mt-3 mb-1">Your California Privacy Rights (CCPA/CPRA)</h3>
            <p className="text-sm">California residents have the right to: know what personal information we collect; request deletion of your data; opt out of the sale or sharing of personal information; and not be discriminated against for exercising these rights.</p>
            <p className="text-sm mt-2">To exercise these rights, contact us at <a href="mailto:mike@thecolyerteam.com" className="text-blue-600 underline">mike@thecolyerteam.com</a> or call <a href="tel:6502888170" className="text-blue-600 underline">(650) 288-8170</a>.</p>
          </div>
        </div>
      )}

      {/* Do Not Sell Modal */}
      {showDns && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setShowDns(false)}>
          <div className="bg-white text-gray-800 rounded-xl max-w-lg w-full max-h-[80vh] overflow-y-auto p-6" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-lg font-bold">Do Not Sell or Share My Personal Information</h2>
              <button onClick={() => setShowDns(false)} className="text-gray-400 hover:text-gray-600 text-2xl">&times;</button>
            </div>
            <p className="text-sm mb-3">Under the California Consumer Privacy Act (CCPA) and California Privacy Rights Act (CPRA), you have the right to opt out of the sale or sharing of your personal information.</p>
            <p className="text-sm mb-3">The Colyer Team does not sell your personal information. We may share limited data with service providers (such as our payment processor) solely to deliver our services.</p>
            <p className="text-sm">To submit a request, contact us at <a href="mailto:mike@thecolyerteam.com" className="text-blue-600 underline">mike@thecolyerteam.com</a> or call <a href="tel:6502888170" className="text-blue-600 underline">(650) 288-8170</a>.</p>
          </div>
        </div>
      )}

      {/* Accessibility Modal */}
      {showAda && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setShowAda(false)}>
          <div className="bg-white text-gray-800 rounded-xl max-w-lg w-full max-h-[80vh] overflow-y-auto p-6" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-4">
              <h2 className="text-lg font-bold">Accessibility Statement</h2>
              <button onClick={() => setShowAda(false)} className="text-gray-400 hover:text-gray-600 text-2xl">&times;</button>
            </div>
            <p className="text-sm mb-3">The Colyer Team is committed to ensuring digital accessibility for people with disabilities. We continually improve the user experience for everyone and apply the relevant accessibility standards.</p>
            <p className="text-sm mb-3">We strive to conform to Web Content Accessibility Guidelines (WCAG) 2.1 Level AA. If you experience any difficulty accessing any part of this website, please contact us.</p>
            <p className="text-sm">Contact: <a href="mailto:mike@thecolyerteam.com" className="text-blue-600 underline">mike@thecolyerteam.com</a> · <a href="tel:6502888170" className="text-blue-600 underline">(650) 288-8170</a></p>
          </div>
        </div>
      )}
    </div>
  )
}
