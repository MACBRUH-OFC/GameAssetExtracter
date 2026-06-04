'use client';
import { useState } from 'react';

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState('');

  const handleUpload = async () => {
    if (!file) return;
    setStatus('Uploading...');
    
    // In a full implementation, you would use @vercel/blob here to upload the file
    // and then call the /api/extract route with the returned URL.
    
    const response = await fetch('/api/extract', {
      method: 'POST',
      body: JSON.stringify({ filename: file.name }),
    });
    
    const data = await response.json();
    setStatus(data.message);
  };

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <div className="bg-white p-8 rounded-xl shadow-md w-full max-w-md">
        <h1 className="text-2xl font-bold mb-4">UBE-GUI Extraction</h1>
        <input 
            type="file" 
            className="mb-4 block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
            onChange={(e) => setFile(e.target.files?.[0] || null)} 
        />
        <button 
          onClick={handleUpload}
          className="w-full bg-blue-600 text-white py-2 rounded-lg font-semibold hover:bg-blue-700 transition"
        >
          Extract Assets
        </button>
        {status && <p className="mt-4 text-sm">{status}</p>}
      </div>
    </main>
  );
}
