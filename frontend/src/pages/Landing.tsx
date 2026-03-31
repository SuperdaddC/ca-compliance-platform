import { Link } from 'react-router-dom'
import Navbar from '../components/Navbar'
import { useAuth } from '../App'
import { PLANS } from '../lib/api'
import type { PlanKey } from '../lib/api'

export default function Landing() {
  const { user: _user } = useAuth()

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
                  <p className="text-xs text-gray-400">{plan.description}</p>
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
        <p className="text-blue-300">2214 Faraday Ave, Carlsbad, CA 92008 · (650) 288-8170 · mike@thecolyerteam.com</p>
        <p className="text-blue-400 mt-4 text-xs">© 2026 The Colyer Team LLC</p>
      </footer>
    </div>
  )
}
