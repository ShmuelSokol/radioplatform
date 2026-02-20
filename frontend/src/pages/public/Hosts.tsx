import { usePublicHosts } from '../../hooks/useUsers';
import type { PublicHost } from '../../types';

function HostCard({ host }: { host: PublicHost }) {
  return (
    <div className="bg-[#12123a] border border-[#2a2a5e] rounded-xl overflow-hidden hover:border-purple-500/50 transition group">
      {/* Photo */}
      <div className="aspect-square bg-[#0a0a28] flex items-center justify-center overflow-hidden">
        {host.photo_url ? (
          <img
            src={host.photo_url}
            alt={host.display_name}
            className="w-full h-full object-cover group-hover:scale-105 transition duration-300"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-purple-900/40 to-indigo-900/40">
            <svg className="w-20 h-20 text-purple-400/40" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z" />
            </svg>
          </div>
        )}
      </div>

      {/* Info */}
      <div className="p-4">
        <h3 className="text-lg font-bold text-white group-hover:text-purple-300 transition">
          {host.display_name}
        </h3>
        {host.title && (
          <p className="text-sm text-purple-400 mt-0.5">{host.title}</p>
        )}
        {host.bio && (
          <p className="text-sm text-gray-400 mt-2 line-clamp-3 leading-relaxed">
            {host.bio}
          </p>
        )}

        {/* Social Links */}
        {host.social_links && Object.keys(host.social_links).length > 0 && (
          <div className="flex gap-3 mt-3 pt-3 border-t border-[#2a2a5e]">
            {host.social_links.twitter && (
              <a
                href={host.social_links.twitter.startsWith('http') ? host.social_links.twitter : `https://twitter.com/${host.social_links.twitter}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-gray-500 hover:text-sky-400 transition"
                title="Twitter / X"
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
                </svg>
              </a>
            )}
            {host.social_links.instagram && (
              <a
                href={host.social_links.instagram.startsWith('http') ? host.social_links.instagram : `https://instagram.com/${host.social_links.instagram}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-gray-500 hover:text-pink-400 transition"
                title="Instagram"
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z" />
                </svg>
              </a>
            )}
            {host.social_links.website && (
              <a
                href={host.social_links.website.startsWith('http') ? host.social_links.website : `https://${host.social_links.website}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-gray-500 hover:text-cyan-400 transition"
                title="Website"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
                </svg>
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Hosts() {
  const { data, isLoading, isError } = usePublicHosts();
  const hosts = data?.hosts ?? [];

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 -my-6 bg-[#080820] font-mono text-[13px] min-h-[calc(100vh-4rem)] text-white p-6">
      {/* Header */}
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-10">
          <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-cyan-400">
            Our DJs & Hosts
          </h1>
          <p className="text-gray-400 mt-2 text-sm">
            Meet the voices behind Kol Bramah Radio
          </p>
        </div>

        {isLoading && (
          <div className="text-center py-20 text-gray-500">
            <div className="inline-block w-6 h-6 border-2 border-purple-500 border-t-transparent rounded-full animate-spin mb-3" />
            <p>Loading hosts...</p>
          </div>
        )}

        {isError && (
          <div className="text-center py-20">
            <p className="text-gray-500 mb-2">Unable to load host profiles.</p>
            <p className="text-sm text-gray-600">Please try again later.</p>
          </div>
        )}

        {!isLoading && !isError && hosts.length === 0 && (
          <div className="text-center py-20">
            <div className="text-6xl mb-4 opacity-20">🎙</div>
            <p className="text-gray-500">No public host profiles yet.</p>
            <p className="text-sm text-gray-600 mt-1">Check back soon!</p>
          </div>
        )}

        {!isLoading && !isError && hosts.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {hosts.map((host) => (
              <HostCard key={host.id} host={host} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}