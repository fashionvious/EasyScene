import { createFileRoute } from "@tanstack/react-router"
import { useState, useEffect, useCallback } from "react"
import useAuth from "@/hooks/useAuth"
import QRCode from "qrcode"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({
    meta: [
      {
        title: "Dashboard - FastAPI Cloud",
      },
    ],
  }),
})

function Dashboard() {
  const { user: currentUser } = useAuth()
  const [qrCodeUrl, setQrCodeUrl] = useState<string | null>(null)
  const [qrCodeKey, setQrCodeKey] = useState<string | null>(null)  // 新增：保存 qrcode_key
  const [isLoginSuccess, setIsLoginSuccess] = useState(false)
  const [isPolling, setIsPolling] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchQRCode = async () => {
    try {
      setError(null)
      const response = await fetch("http://127.0.0.1:8000/api/v1/bilibili/get-qr-code")
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      
      const data = await response.json()
      console.log("API返回数据:", data)
      
      if (data.url && data.qrcode_key) {
        // 保存 qrcode_key
        setQrCodeKey(data.qrcode_key)
        
        const qrImageDataUrl = await QRCode.toDataURL(data.url, {
          width: 200,
          margin: 2,
        })
        setQrCodeUrl(qrImageDataUrl)
        setIsPolling(true)
        setIsLoginSuccess(false)
      } else {
        setError("获取二维码失败，请重试")
      }
    } catch (err) {
      setError("网络错误，请稍后重试")
      console.error(err)
    }
  }

  // 轮询检查登录状态 - 修改：传递 qrcode_key 参数
  const checkLoginStatus = useCallback(async () => {
    if (!qrCodeKey) return  // 如果没有 key，不执行轮询
    
    try {
      const response = await fetch(
        `http://127.0.0.1:8000/api/v1/bilibili/poll-qr-code?qrcode_key=${qrCodeKey}`
      )
      const data = await response.json()
      
      if (data.logged_in || data.code === 0) {
        setIsLoginSuccess(true)
        setQrCodeUrl(null)
        setQrCodeKey(null)  // 清除 key
        setIsPolling(false)
      }
    } catch (err) {
      console.error("检查登录状态失败:", err)
    }
  }, [qrCodeKey])

  useEffect(() => {
    if (!isPolling || !qrCodeKey) return

    const interval = setInterval(checkLoginStatus, 2000)
    return () => clearInterval(interval)
  }, [isPolling, qrCodeKey, checkLoginStatus])

  return (
    <div className="p-6 md:p-8">
      <div>
        <h1 className="text-2xl truncate max-w-sm">
          Hi, {currentUser?.full_name || currentUser?.email} 👋
        </h1>
        <p className="text-muted-foreground">
          Welcome back, nice to see you again!!!
        </p>
        <br/> 
        <h2 className="text-5xl font-black mb-5">
        Bilibili 授权登录
        </h2>
           
      {/* 提示文字 */}
      <p className="text-2xl  mb-8">
        为了使用B站相关功能，请先授权登录
      </p>

       {/* 登录成功提示 */}
       {isLoginSuccess ? (
        <div className="text-green-400 text-xl font-semibold">
          您已登录b站成功！
        </div>
      ) : (
        <>
          {/* 获取二维码按钮 */}
          {!qrCodeUrl && (
            <button
              onClick={fetchQRCode}
              className="px-6 py-3 bg-pink-500 hover:bg-pink-600 text-white font-semibold rounded-lg transition-colors"
            >
              获取B站登陆二维码
            </button>
          )}

          {/* 二维码显示区域 */}
          {qrCodeUrl && (
            <div className="flex flex-col items-center">
              <img 
                src={qrCodeUrl} 
                alt="B站登录二维码" 
                className="w-48 h-48 border-4 border-white rounded-lg"
              />
              <p className="text-white mt-4 animate-pulse">
                请使用B站APP扫描二维码...
              </p>
            </div>
          )}

          {/* 错误提示 */}
          {error && (
            <p className="text-red-400 mt-4">{error}</p>
          )}
        </>
      )}
      </div>
    </div>
  )
}
