import urllib.request
import urllib.parse
import json
import sys
import ssl

def get_weather_forecast():
    # 请瑞雪使用
    api_key = "d92b0294d0ee41e587933457260603" 
    base_url = "https://api.weatherapi.com/v1/forecast.json"
    
    # 创建未验证的 SSL 上下文（解决某些环境下的证书问题）
    ssl_context = ssl._create_unverified_context()

    # 获取命令行参数作为查询城市，默认为深圳
    city = sys.argv[1] if len(sys.argv) > 1 else "深圳"

    # 构建查询参数
    params = {
        "q": city,
        "days": "3",
        "key": api_key
    }
    
    # URL 编码参数
    query_string = urllib.parse.urlencode(params)
    full_url = f"{base_url}?{query_string}"
    
    # 创建请求对象
    req = urllib.request.Request(
        full_url, 
        headers={'Accept': 'application/json'}
    )
    
    # 发送请求
    try:
        with urllib.request.urlopen(req, context=ssl_context) as response:
            # 读取响应
            response_data = response.read().decode('utf-8')
            
            # 解析 JSON
            data = json.loads(response_data)
            
            # 打印格式化后的结果
            print("=== 天气预报 ===")
            print(f"位置: {data['location']['name']}, {data['location']['country']}")
            print(f"当前时间: {data['location']['localtime']}")
            
            print("\n--- 当前天气 ---")
            print(f"温度: {data['current']['temp_c']}°C")
            print(f"天气状况: {data['current']['condition']['text']}")
            print(f"湿度: {data['current']['humidity']}%")
            print(f"风速: {data['current']['wind_kph']} km/h")
            
            print("\n--- 未来预报 ---")
            for day in data['forecast']['forecastday']:
                date = day['date']
                max_temp = day['day']['maxtemp_c']
                min_temp = day['day']['mintemp_c']
                condition = day['day']['condition']['text']
                print(f"日期: {date} | 温度: {min_temp}°C - {max_temp}°C | 天气: {condition}")
                
    except urllib.error.HTTPError as e:
        print(f"HTTP 错误: {e.code} - {e.reason}")
        print("请检查您的 API Key 是否正确。")
    except urllib.error.URLError as e:
        print(f"网络错误: {e.reason}")
    except json.JSONDecodeError:
        print("无法解析服务器返回的 JSON 数据")
    except Exception as e:
        print(f"发生未知错误: {e}")

if __name__ == "__main__":
    get_weather_forecast()
