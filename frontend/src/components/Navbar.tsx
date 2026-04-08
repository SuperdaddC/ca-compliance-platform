import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../App'

export default function Navbar() {
  const { user, role, signOut } = useAuth()
  const navigate = useNavigate()

  function handlePricingClick(e: React.MouseEvent) {
    e.preventDefault()
    const el = document.getElementById('pricing')
    if (el) {
      el.scrollIntoView({ behavior: 'smooth' })
    } else {
      navigate('/')
      setTimeout(() => {
        document.getElementById('pricing')?.scrollIntoView({ behavior: 'smooth' })
      }, 100)
    }
  }

  return (
    <nav className="bg-white border-b border-gray-200">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 flex items-center justify-between h-14">
        <Link to="/" className="text-brand-blue font-bold text-lg tracking-tight">
          ComplyWithJudy
        </Link>

        <div className="flex items-center gap-4">
          <Link to="/scan" className="text-sm font-medium text-gray-600 hover:text-brand-blue transition-colors">
            Scan
          </Link>
          <Link to="/checks" className="text-sm font-medium text-gray-600 hover:text-brand-blue transition-colors">
            Checks
          </Link>
          <a href="/#pricing" onClick={handlePricingClick} className="text-sm font-medium text-gray-600 hover:text-brand-blue transition-colors cursor-pointer">
            Pricing
          </a>
          {user ? (
            <>
              <Link to="/dashboard" className="text-sm font-medium text-gray-600 hover:text-brand-blue transition-colors">
                Dashboard
              </Link>
              {role === 'admin' && (
                <Link to="/admin/queue" className="text-sm font-medium text-purple-600 hover:text-purple-800 transition-colors">
                  Admin
                </Link>
              )}
              <button
                onClick={signOut}
                className="text-sm font-medium text-gray-500 hover:text-red-600 transition-colors"
              >
                Sign out
              </button>
            </>
          ) : (
            <Link
              to="/login"
              className="text-sm font-semibold text-brand-blue hover:text-brand-gold transition-colors"
            >
              Log in
            </Link>
          )}
        </div>
      </div>
    </nav>
  )
}
