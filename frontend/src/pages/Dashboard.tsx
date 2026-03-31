import { useAuth } from '../App'
import Navbar from '../components/Navbar'

export default function Dashboard() {
  const { user } = useAuth()

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />
      <div className="max-w-4xl mx-auto px-4 py-12">
        <h1 className="text-2xl font-bold text-gray-900 mb-2">Dashboard</h1>
        <p className="text-gray-500 mb-8">Welcome back, {user?.email ?? 'user'}.</p>
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center text-gray-400">
          Your scan history will appear here.
        </div>
      </div>
    </div>
  )
}
